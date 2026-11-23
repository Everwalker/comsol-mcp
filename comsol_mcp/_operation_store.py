"""Durable, serialized operation/job ledger."""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import UUID, uuid4

from ._job_event_digest import CanonicalJobEventDigests
from ._job_project_scope import consistent_project_id
from ._json_error_stream import project_error_chunks


# ``resume_claims`` is an additive SQLite table.  Keep the on-disk version at
# 1 because older v1 readers ignore unknown tables and preserve them; the
# production ProcessLock prevents mixed-version daemons from coordinating the
# same project concurrently.  Older binaries do not implement resume and are
# never permitted to replay a claimed continuation after restart.
SCHEMA_VERSION = 1
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST")
JOB_EVENT_SNAPSHOT_SCHEMA = "operation-store-job-event-snapshot/v1"
JOB_EVENT_SNAPSHOT_PAGE_SIZE = 1000
JOB_EVENT_SNAPSHOT_MAX_PAGES = 128
METADATA_TABLES = {
    "sessions": "session_id",
    "runtimes": "runtime_id",
    "revisions": "model_key",
    "artifacts": "artifact_id",
    "checkpoints": "checkpoint_id",
}
ARTIFACT_SCOPE_INDEX = "idx_artifacts_v2_project_scope"
_ARTIFACT_SCOPE_JSON_FIELDS = (
    "schema_version", "project_id", "project_root_identity",
    "host_identity", "engine_host_identity",
)


def _artifact_json_value(field: str) -> str:
    """A malformed historical metadata row must evaluate to NULL, not abort a page."""
    if field not in _ARTIFACT_SCOPE_JSON_FIELDS and field not in {"role", "classification"}:
        raise ValueError("unsupported artifact metadata field")
    return f"CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.{field}') END"


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
HOST_CONTROL_ADMISSION_TABLE_COLUMNS = (
    ("admission_id", "TEXT", 1, 1, None),
    ("operation_id", "TEXT", 1, 0, None),
    ("job_id", "TEXT", 1, 0, None),
    ("project_id", "TEXT", 1, 0, None),
    ("host_id", "TEXT", 1, 0, None),
    ("root_identity_json", "TEXT", 1, 0, None),
    ("profile_sha256", "TEXT", 1, 0, None),
    ("plan_sha256", "TEXT", 1, 0, None),
    ("source_plan_sha256", "TEXT", 0, 0, None),
    ("source_receipt_sha256", "TEXT", 0, 0, None),
    ("phase", "TEXT", 1, 0, None),
    ("authorization_ref_sha256", "TEXT", 1, 0, None),
    ("scope_sha256", "TEXT", 1, 0, None),
    ("baseline_json", "TEXT", 1, 0, None),
    ("baseline_sha256", "TEXT", 1, 0, None),
    ("state", "TEXT", 1, 0, None),
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
    "NOT_EVALUATED", "UNVERIFIED", "PARTIAL", "ACCEPTED", "INITIAL_OUTPUT_ACCEPTED", "REJECTED", "UNKNOWN",
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


def _job_event_snapshot_sha256(value: Any) -> str:
    """Use the same strict canonical JSON encoding as stage evidence hashes."""
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


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
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_operations_request_id ON operations(request_id)")
            # Existing artifact metadata predates project-scoped reads. This
            # additive index lets those reads seek directly into one v2
            # project/root/host range. CASE keeps malformed legacy JSON
            # fail-closed during index creation and query evaluation.
            scope_terms = ", ".join(f"({_artifact_json_value(field)})" for field in _ARTIFACT_SCOPE_JSON_FIELDS)
            self.db.execute(
                f"CREATE INDEX IF NOT EXISTS {ARTIFACT_SCOPE_INDEX} "
                f"ON artifacts({scope_terms}, artifact_id)"
            )
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
            # Privileged host mutations use a dedicated atomic admission row.
            # A generic operation/job row, even one named host.change.apply,
            # cannot authorize the internal executor without this binding.
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS host_control_admissions("
                "admission_id TEXT PRIMARY KEY NOT NULL,operation_id TEXT NOT NULL UNIQUE,"
                "job_id TEXT NOT NULL UNIQUE,project_id TEXT NOT NULL,host_id TEXT NOT NULL,"
                "root_identity_json TEXT NOT NULL,profile_sha256 TEXT NOT NULL,plan_sha256 TEXT NOT NULL,"
                "source_plan_sha256 TEXT,source_receipt_sha256 TEXT,phase TEXT NOT NULL,"
                "authorization_ref_sha256 TEXT NOT NULL UNIQUE,scope_sha256 TEXT NOT NULL,"
                "baseline_json TEXT NOT NULL,baseline_sha256 TEXT NOT NULL,state TEXT NOT NULL,"
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            host_object = self.db.execute(
                "SELECT type FROM sqlite_master WHERE name='host_control_admissions'"
            ).fetchone()
            observed_host_columns = tuple(
                (row[1], (row[2] or "").upper(), row[3], row[5], row[4])
                for row in self.db.execute("PRAGMA table_info(host_control_admissions)").fetchall()
            )
            unique_host_columns: set[tuple[str, ...]] = set()
            for index in self.db.execute("PRAGMA index_list(host_control_admissions)").fetchall():
                if index[2] != 1 or (len(index) > 4 and index[4] != 0):
                    continue
                name = str(index[1]).replace("'", "''")
                columns = self.db.execute(f"PRAGMA index_info('{name}')").fetchall()
                if len(columns) == 1 and columns[0][1] >= 0 and columns[0][2] is not None:
                    unique_host_columns.add((columns[0][2],))
            if (host_object is None or host_object[0] != "table"
                    or observed_host_columns != HOST_CONTROL_ADMISSION_TABLE_COLUMNS
                    or not {("admission_id",), ("operation_id",), ("job_id",),
                            ("authorization_ref_sha256",)} <= unique_host_columns):
                raise RuntimeError("invalid host_control_admissions schema")
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

    def begin_host_control_operation(
        self,
        *,
        authority: Any,
        provider: Any,
        profile: Any,
        phase: str,
        plan_sha256: str,
        authorization_ref: str,
        baseline: dict[str, Any],
        source_plan_sha256: str | None = None,
        source_receipt_sha256: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically authorize and admit one internal host-control operation.

        This is intentionally separate from ``begin``. Caller-created generic
        operation/job metadata never creates a host-control admission. The
        trusted scoped provider is checked under the same SQLite write lock
        that consumes the one-use authorization-reference digest and stores
        the exact host/profile/root/plan binding plus its observed baseline.
        """
        from ._host_scoped_provider import HostScopedProvider, scope_sha256
        from ._project_authority import ProjectAuthority
        from ._w26_os_resources import (
            HostResourceProfile, ResourcePolicyError, _path_key,
            _sha256 as resource_sha256, validate_profile,
        )

        if (type(authority) is not ProjectAuthority or authority.store is not self
                or type(provider) is not HostScopedProvider
                or authority.authorization_verifier is not provider
                or type(profile) is not HostResourceProfile):
            raise ResourcePolicyError(
                "HOST_CONTROL_TRUSTED_COMPONENT_REQUIRED",
                "host mutation admission requires the existing authority, pinned HostScopedProvider, and typed profile",
            )
        try:
            validate_profile(profile)
        except (TypeError, ValueError) as exc:
            raise ResourcePolicyError("HOST_CONTROL_PROFILE_INVALID", "host mutation profile is invalid") from exc
        if (profile.platform != "windows" or profile.windows_isolation is None
                or phase not in {"APPLY", "RESTORE"}
                or not isinstance(plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_sha256)
                or not isinstance(authorization_ref, str) or not authorization_ref
                or len(authorization_ref) > 512
                or any(ord(char) < 32 for char in authorization_ref)
                or not isinstance(baseline, dict)):
            raise ResourcePolicyError("HOST_CONTROL_ADMISSION_INVALID", "host mutation admission fields are malformed")
        if phase == "APPLY":
            if source_plan_sha256 is not None or source_receipt_sha256 is not None:
                raise ResourcePolicyError("HOST_CONTROL_ADMISSION_INVALID", "apply admission cannot name a restore source")
            action = "host.change.apply"
        else:
            if (not isinstance(source_plan_sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", source_plan_sha256)
                    or not isinstance(source_receipt_sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", source_receipt_sha256)):
                raise ResourcePolicyError("HOST_CONTROL_ADMISSION_INVALID", "restore admission must bind its source plan and receipt")
            action = "host.change.restore"
        root_identity = profile.root.as_dict()
        scope = {
            "root": root_identity,
            "host_id": profile.host_id,
            "profile_sha256": profile.sha256,
            "plan_sha256": plan_sha256,
            "phase": phase,
            "source_plan_sha256": source_plan_sha256,
            "source_receipt_sha256": source_receipt_sha256,
        }
        try:
            scope_digest = scope_sha256(profile.project_id, action, scope)
            baseline_json = json.dumps(baseline, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ResourcePolicyError("HOST_CONTROL_ADMISSION_INVALID", "host baseline/scope is not canonical") from exc
        if len(baseline_json.encode("utf-8")) > 1024 * 1024:
            raise ResourcePolicyError("HOST_CONTROL_ADMISSION_INVALID", "host baseline exceeds its durable record bound")
        baseline_sha256 = hashlib.sha256(baseline_json.encode("utf-8")).hexdigest()
        authorization_ref_sha256 = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()
        admission_id = str(uuid4())
        operation_id, job_id = str(uuid4()), str(uuid4())
        request_id = "host-control-" + str(uuid4())
        idempotency_key = "host-control-" + str(uuid4())
        operation_name = f"host.change.{phase.lower()}"
        root_path = root_identity["path"]
        request_hash = resource_sha256({
            "operation": operation_name,
            "project_id": profile.project_id,
            "host_id": profile.host_id,
            "root": root_identity,
            "profile_sha256": profile.sha256,
            "plan_sha256": plan_sha256,
            "phase": phase,
            "source_plan_sha256": source_plan_sha256,
            "source_receipt_sha256": source_receipt_sha256,
            "baseline_sha256": baseline_sha256,
            "scope_sha256": scope_digest,
        })
        internal_binding = {
            "schema": "comsol-mcp.internal-host-control-operation/1",
            "admission_id": admission_id,
            "phase": phase,
            "project_id": profile.project_id,
            "host_id": profile.host_id,
            "root_identity": root_identity,
            "profile_sha256": profile.sha256,
            "plan_sha256": plan_sha256,
            "source_plan_sha256": source_plan_sha256,
            "source_receipt_sha256": source_receipt_sha256,
        }
        metadata = {"operation": operation_name, "project_id": profile.project_id,
                    "project_root_identity": profile.root.file_id,
                    "project_root_volume_id": profile.root.volume_id,
                    "project_root_owner_id": profile.root.owner_id,
                    "host_id": profile.host_id, "host_control": internal_binding}
        try:
            with self.lock:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    # Recheck project permission, workspace binding, exact
                    # host/root/profile scope, and provider-observed expiry
                    # while the authorization ref is being consumed.
                    project = authority.authorize_operation(profile.project_id, "host_control")
                    if _path_key(project.get("workspace", ""), "windows") != _path_key(root_path, "windows"):
                        raise ResourcePolicyError("HOST_CONTROL_ROOT_AUTHORITY_MISMATCH",
                                                  "profile root differs from the authorized project workspace")
                    if provider.verify_scope(profile.project_id, action, scope, authorization_ref) is not True:
                        raise ResourcePolicyError("HOST_CONTROL_AUTHORIZATION_REQUIRED",
                                                  "the pinned host provider rejected this exact host mutation scope")
                    replay = self.db.execute(
                        "SELECT admission_id,phase,scope_sha256 FROM host_control_admissions "
                        "WHERE authorization_ref_sha256=?", (authorization_ref_sha256,),
                    ).fetchone()
                    if replay is not None:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_AUTHORIZATION_REPLAYED",
                            "host mutation authorization reference was already consumed",
                            details={"admission_id": replay["admission_id"], "phase": replay["phase"]},
                        )
                    operation_metadata = json.dumps(metadata, sort_keys=True, ensure_ascii=False,
                                                    separators=(",", ":"), allow_nan=False)
                    timeouts = "{}"
                    self.db.execute(
                        "INSERT INTO operations(operation_id,request_id,idempotency_key,request_hash,operation,status,metadata,effective_timeouts) "
                        "VALUES(?,?,?,?,?,'QUEUED',?,?)",
                        (operation_id, request_id, idempotency_key, request_hash, operation_name,
                         operation_metadata, timeouts),
                    )
                    self.db.execute(
                        "INSERT INTO jobs(job_id,operation_id,status,metadata,effective_timeouts) "
                        "VALUES(?,?,'QUEUED',?,?)",
                        (job_id, operation_id, operation_metadata, timeouts),
                    )
                    self.db.execute(
                        "INSERT INTO host_control_admissions(admission_id,operation_id,job_id,project_id,host_id,"
                        "root_identity_json,profile_sha256,plan_sha256,source_plan_sha256,source_receipt_sha256,phase,"
                        "authorization_ref_sha256,scope_sha256,baseline_json,baseline_sha256,state) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ADMITTED')",
                        (admission_id, operation_id, job_id, profile.project_id, profile.host_id,
                         json.dumps(root_identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                         profile.sha256, plan_sha256, source_plan_sha256, source_receipt_sha256, phase,
                         authorization_ref_sha256, scope_digest, baseline_json, baseline_sha256),
                    )
                    self.db.execute(
                        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                        (job_id, "HostControlAdmissionCreated", json.dumps({
                            "schema": "comsol-mcp.internal-host-control-admission-event/1",
                            "admission_id": admission_id, "phase": phase,
                            "profile_sha256": profile.sha256, "plan_sha256": plan_sha256,
                            "scope_sha256": scope_digest,
                            "authorization_ref_sha256": authorization_ref_sha256,
                            "baseline_sha256": baseline_sha256,
                        }, sort_keys=True, separators=(",", ":"))),
                    )
                    self.db.execute("COMMIT")
                except BaseException:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise
                operation_row = self.db.execute(
                    "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
                ).fetchone()
                admission_row = self.db.execute(
                    "SELECT * FROM host_control_admissions WHERE admission_id=?", (admission_id,)
                ).fetchone()
                if operation_row is None or admission_row is None:
                    raise ResourcePolicyError("HOST_CONTROL_ADMISSION_READBACK_UNKNOWN",
                                              "committed host mutation admission failed exact readback")
                record = self._op(operation_row)
                record["job_id"] = job_id
                admission = dict(admission_row)
                if (record["status"] != "QUEUED" or admission["state"] != "ADMITTED"
                        or admission["authorization_ref_sha256"] != authorization_ref_sha256
                        or admission["baseline_sha256"] != baseline_sha256
                        or json.loads(admission["baseline_json"]) != baseline):
                    raise ResourcePolicyError("HOST_CONTROL_ADMISSION_READBACK_UNKNOWN",
                                              "committed host mutation admission differs from exact bytes")
                return record, admission
        except ResourcePolicyError:
            raise
        except Exception as exc:
            raise ResourcePolicyError("HOST_CONTROL_ADMISSION_UNKNOWN",
                                      "host mutation admission transaction failed closed",
                                      details={"cause_type": type(exc).__name__}) from exc

    def append_host_control_mutation_event(
        self,
        *,
        admission_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically append a hash-chained mutation event and advance its state.

        The V1 filter-only WFP executor uses this before/after its native call.
        A committed intent is never replayed after a crash; only an exact
        readback event can move it to APPLIED/RESTORED. The durable event is
        stored in the existing OperationStore job ledger, not a side database.
        """
        from ._w26_os_resources import ResourcePolicyError

        event_contracts = {
            "APPLY_INTENT": ("APPLY", "ADMITTED", "APPLY_INTENT",
                             {"action_id", "filter_key", "desired_identity_sha256", "dependency_sha256"}),
            "APPLY_READBACK_VERIFIED": ("APPLY", "APPLY_INTENT", "APPLIED",
                                        {"action_id", "filter_key", "desired_identity_sha256",
                                         "created_resource_sha256", "dependency_sha256"}),
            "RESTORE_INTENT": ("RESTORE", "ADMITTED", "RESTORE_INTENT",
                               {"action_id", "filter_key", "expected_resource_sha256", "dependency_sha256"}),
            "RESTORE_READBACK_VERIFIED": ("RESTORE", "RESTORE_INTENT", "RESTORED",
                                          {"action_id", "filter_key", "removed_resource_sha256",
                                           "expected_resource_sha256", "dependency_sha256"}),
            "RECOVERY_REQUIRED": (None, None, "RECOVERY_REQUIRED",
                                  {"reason_code", "failed_event", "action_id", "filter_key"}),
        }
        if (not isinstance(admission_id, str) or not admission_id
                or event not in event_contracts or not isinstance(payload, dict)):
            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                      "host mutation event identity or type is malformed")
        allowed_keys = event_contracts[event][3]
        if set(payload) != allowed_keys:
            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                      "host mutation event payload fields are outside the closed contract")
        digest_fields = {"desired_identity_sha256", "created_resource_sha256",
                         "expected_resource_sha256", "removed_resource_sha256", "dependency_sha256"}
        for key, value in payload.items():
            if key in digest_fields and (not isinstance(value, str)
                                         or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                          "host mutation event digest is malformed")
            if key in {"action_id", "filter_key", "reason_code", "failed_event"} and (
                    not isinstance(value, str) or not value or len(value) > 256
                    or any(ord(char) < 32 for char in value)):
                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                          "host mutation event identifier is malformed")
        encoded_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False)
        if len(encoded_payload.encode("utf-8")) > 16 * 1024:
            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                      "host mutation event payload exceeds its durable bound")

        def canonical(value: Any) -> str:
            return json.dumps(value, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False)

        def digest(value: Any) -> str:
            return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

        event_name = "HostControlMutationJournalV1"
        try:
            with self.lock:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    row = self.db.execute(
                        "SELECT a.*,j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
                        "o.status AS operation_status,o.operation,o.metadata AS operation_metadata "
                        "FROM host_control_admissions a "
                        "JOIN jobs j ON j.job_id=a.job_id AND j.operation_id=a.operation_id "
                        "JOIN operations o ON o.operation_id=a.operation_id "
                        "WHERE a.admission_id=?", (admission_id,),
                    ).fetchone()
                    if row is None:
                        raise ResourcePolicyError("HOST_CONTROL_ADMISSION_UNKNOWN",
                                                  "host mutation admission is absent from OperationStore")
                    if self.db.execute(
                            "SELECT 1 FROM job_events WHERE job_id=? AND event=? LIMIT 1",
                            (row["job_id"], "HostControlMutationJournalV2"),
                    ).fetchone() is not None:
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                                                  "V1 and V2 host mutation journals cannot be mixed")
                    try:
                        root_identity = json.loads(row["root_identity_json"])
                        job_metadata = json.loads(row["job_metadata"] or "{}")
                        operation_metadata = json.loads(row["operation_metadata"] or "{}")
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ResourcePolicyError("HOST_CONTROL_BINDING_UNKNOWN",
                                                  "host mutation durable scope metadata is malformed") from exc
                    binding = {
                        "schema": "comsol-mcp.internal-host-control-operation/1",
                        "admission_id": admission_id,
                        "phase": row["phase"],
                        "project_id": row["project_id"],
                        "host_id": row["host_id"],
                        "root_identity": root_identity,
                        "profile_sha256": row["profile_sha256"],
                        "plan_sha256": row["plan_sha256"],
                        "source_plan_sha256": row["source_plan_sha256"],
                        "source_receipt_sha256": row["source_receipt_sha256"],
                    }
                    for metadata in (job_metadata, operation_metadata):
                        if (metadata.get("host_control") != binding
                                or metadata.get("project_id") != row["project_id"]
                                or metadata.get("host_id") != row["host_id"]
                                or metadata.get("project_root_identity") != root_identity.get("file_id")
                                or metadata.get("project_root_volume_id") != root_identity.get("volume_id")
                                or metadata.get("project_root_owner_id") != root_identity.get("owner_id")):
                            raise ResourcePolicyError("HOST_CONTROL_BINDING_MISMATCH",
                                                      "job and operation no longer match the exact admitted host scope")
                    phase, required_state, new_state, _keys = event_contracts[event]
                    current_state = str(row["state"])
                    if row["job_status"] != "RUNNING" or row["operation_status"] != "RUNNING":
                        raise ResourcePolicyError("HOST_CONTROL_STATE_CONFLICT",
                                                  "host mutation journal can advance only while its admitted operation is RUNNING")
                    if event == "RECOVERY_REQUIRED":
                        if current_state not in {"APPLY_INTENT", "RESTORE_INTENT"}:
                            raise ResourcePolicyError("HOST_CONTROL_STATE_CONFLICT",
                                                      "recovery can only be recorded after a durable mutation intent")
                        if (payload["failed_event"] not in {"APPLY_INTENT", "RESTORE_INTENT"}
                                or payload["failed_event"] != current_state):
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                                      "recovery event must identify the current exact durable intent")
                    elif row["phase"] != phase or current_state != required_state:
                        raise ResourcePolicyError("HOST_CONTROL_STATE_CONFLICT",
                                                  "host mutation event cannot replay or skip a durable state")
                    if row["operation"] != f"host.change.{str(row['phase']).lower()}":
                        raise ResourcePolicyError("HOST_CONTROL_BINDING_MISMATCH",
                                                  "admitted operation name differs from its privileged phase")
                    records = self.db.execute(
                        "SELECT id,metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id",
                        (row["job_id"], event_name),
                    ).fetchall()
                    previous = "0" * 64
                    journal_state = "ADMITTED"
                    journal_action: str | None = None
                    journal_filter: str | None = None
                    journal_intent: dict[str, Any] | None = None
                    phase_events = {
                        "APPLY": {"APPLY_INTENT", "APPLY_READBACK_VERIFIED"},
                        "RESTORE": {"RESTORE_INTENT", "RESTORE_READBACK_VERIFIED"},
                    }[str(row["phase"])]
                    event_payload_keys = {name: contract[3] for name, contract in event_contracts.items()}
                    for sequence, record_row in enumerate(records):
                        try:
                            prior = json.loads(record_row["metadata"])
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "host mutation journal row is malformed") from exc
                        expected_fields = {"schema", "admission_id", "job_id", "operation_id", "project_id",
                                           "host_id", "root_identity", "profile_sha256", "plan_sha256",
                                           "source_plan_sha256", "source_receipt_sha256", "phase", "sequence",
                                           "previous_sha256", "event", "payload", "record_sha256"}
                        if (not isinstance(prior, dict) or set(prior) != expected_fields
                                or prior.get("schema") != "comsol-mcp.host-control-mutation-journal/1"
                                or prior.get("sequence") != sequence or type(prior.get("sequence")) is not int
                                or prior.get("previous_sha256") != previous
                                or prior.get("admission_id") != admission_id
                                or prior.get("job_id") != row["job_id"]
                                or prior.get("operation_id") != row["operation_id"]
                                or prior.get("project_id") != row["project_id"]
                                or prior.get("host_id") != row["host_id"]
                                or prior.get("root_identity") != root_identity
                                or prior.get("profile_sha256") != row["profile_sha256"]
                                or prior.get("plan_sha256") != row["plan_sha256"]
                                or prior.get("source_plan_sha256") != row["source_plan_sha256"]
                                or prior.get("source_receipt_sha256") != row["source_receipt_sha256"]
                                or prior.get("phase") != row["phase"]
                                or not isinstance(prior.get("payload"), dict)
                                or not isinstance(prior.get("record_sha256"), str)):
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "host mutation journal chain binding is invalid")
                        body = {key: value for key, value in prior.items() if key != "record_sha256"}
                        if digest(body) != prior["record_sha256"]:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "host mutation journal record hash differs from its payload")
                        prior_event = prior["event"]
                        prior_payload = prior["payload"]
                        if (prior_event not in phase_events | {"RECOVERY_REQUIRED"}
                                or set(prior_payload) != event_payload_keys[prior_event]):
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "host mutation journal has an impossible event or payload")
                        for key, value in prior_payload.items():
                            if key in digest_fields and (not isinstance(value, str)
                                                         or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "host mutation journal contains a malformed digest")
                            if key in {"action_id", "filter_key", "reason_code", "failed_event"} and (
                                    not isinstance(value, str) or not value or len(value) > 256
                                    or any(ord(char) < 32 for char in value)):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "host mutation journal contains a malformed identifier")
                        if prior_event == "RECOVERY_REQUIRED":
                            if (journal_state not in {"APPLY_INTENT", "RESTORE_INTENT"}
                                    or prior_payload.get("failed_event") != journal_state
                                    or journal_intent is None
                                    or prior_payload.get("action_id") != journal_intent.get("action_id")
                                    or prior_payload.get("filter_key") != journal_intent.get("filter_key")):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "host recovery event does not match the preceding intent")
                            journal_state = "RECOVERY_REQUIRED"
                        elif prior_event == ("APPLY_INTENT" if row["phase"] == "APPLY" else "RESTORE_INTENT"):
                            if journal_state != "ADMITTED":
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "host mutation intent is duplicated or out of sequence")
                            journal_state = prior_event
                            journal_intent = dict(prior_payload)
                        else:
                            expected_intent = "APPLY_INTENT" if row["phase"] == "APPLY" else "RESTORE_INTENT"
                            if (journal_state != expected_intent or journal_intent is None
                                    or prior_payload.get("action_id") != journal_intent.get("action_id")
                                    or prior_payload.get("filter_key") != journal_intent.get("filter_key")):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "host mutation readback has no matching durable intent")
                            if row["phase"] == "APPLY":
                                matching = (prior_payload.get("desired_identity_sha256")
                                            == journal_intent.get("desired_identity_sha256")
                                            and prior_payload.get("dependency_sha256")
                                            == journal_intent.get("dependency_sha256"))
                            else:
                                matching = (prior_payload.get("expected_resource_sha256")
                                            == journal_intent.get("expected_resource_sha256")
                                            and prior_payload.get("dependency_sha256")
                                            == journal_intent.get("dependency_sha256")
                                            and prior_payload.get("removed_resource_sha256")
                                            == journal_intent.get("expected_resource_sha256"))
                            if not matching:
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "host readback does not confirm the exact intent resource/dependency")
                            journal_state = "APPLIED" if row["phase"] == "APPLY" else "RESTORED"
                        if (not isinstance(prior_payload.get("action_id"), str)
                                or not isinstance(prior_payload.get("filter_key"), str)
                                or (journal_action is not None and prior_payload["action_id"] != journal_action)
                                or (journal_filter is not None and prior_payload["filter_key"] != journal_filter)):
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "host mutation journal action identity changed between events")
                        journal_action = prior_payload["action_id"]
                        journal_filter = prior_payload["filter_key"]
                        previous = prior["record_sha256"]
                    if journal_state != current_state:
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                  "host mutation admission state differs from its journal history")
                    if records and (payload["action_id"] != journal_action
                                    or payload["filter_key"] != journal_filter):
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                                  "host mutation event changed the action identity already journaled")
                    if event in {"APPLY_READBACK_VERIFIED", "RESTORE_READBACK_VERIFIED"}:
                        if journal_intent is None:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_STATE_UNKNOWN",
                                                      "host mutation readback has no durable intent")
                        if row["phase"] == "APPLY":
                            matching_current = (
                                payload.get("desired_identity_sha256") == journal_intent.get("desired_identity_sha256")
                                and payload.get("dependency_sha256") == journal_intent.get("dependency_sha256"))
                        else:
                            matching_current = (
                                payload.get("expected_resource_sha256") == journal_intent.get("expected_resource_sha256")
                                and payload.get("dependency_sha256") == journal_intent.get("dependency_sha256")
                                and payload.get("removed_resource_sha256") == journal_intent.get("expected_resource_sha256"))
                        if not matching_current:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                                      "host readback payload does not confirm the exact durable intent")
                    if event == "RECOVERY_REQUIRED" and (
                            journal_intent is None or payload.get("failed_event") != current_state
                            or payload.get("action_id") != journal_intent.get("action_id")
                            or payload.get("filter_key") != journal_intent.get("filter_key")):
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                                  "recovery payload does not identify the current durable intent")
                    body = {
                        "schema": "comsol-mcp.host-control-mutation-journal/1",
                        "admission_id": admission_id, "job_id": row["job_id"],
                        "operation_id": row["operation_id"], "project_id": row["project_id"],
                        "host_id": row["host_id"], "root_identity": root_identity,
                        "profile_sha256": row["profile_sha256"], "plan_sha256": row["plan_sha256"],
                        "source_plan_sha256": row["source_plan_sha256"],
                        "source_receipt_sha256": row["source_receipt_sha256"],
                        "phase": row["phase"], "sequence": len(records),
                        "previous_sha256": previous, "event": event, "payload": payload,
                    }
                    record = {**body, "record_sha256": digest(body)}
                    self.db.execute(
                        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                        (row["job_id"], event_name, canonical(record)),
                    )
                    updated = self.db.execute(
                        "UPDATE host_control_admissions SET state=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE admission_id=? AND state=?",
                        (new_state, admission_id, current_state),
                    )
                    if updated.rowcount != 1:
                        raise ResourcePolicyError("HOST_CONTROL_STATE_CONFLICT",
                                                  "host mutation admission state changed during event append")
                    self.db.execute("COMMIT")
                except BaseException:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise
                row = self.db.execute(
                    "SELECT id,metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id DESC LIMIT 1",
                    (row["job_id"], event_name),
                ).fetchone()
                admission = self.db.execute(
                    "SELECT state FROM host_control_admissions WHERE admission_id=?", (admission_id,)
                ).fetchone()
                if (row is None or json.loads(row["metadata"]) != record
                        or admission is None or admission["state"] != new_state):
                    raise ResourcePolicyError("HOST_CONTROL_JOURNAL_READBACK_UNKNOWN",
                                              "durable host mutation event failed exact readback")
                return {"id": int(row["id"]), **record, "state": new_state}
        except ResourcePolicyError:
            raise
        except Exception as exc:
            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_UNKNOWN",
                                      "host mutation journal transaction failed closed",
                                      details={"cause_type": type(exc).__name__}) from exc

    def append_host_control_mutation_step_event(
        self,
        *,
        admission_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one resource-scoped mutation step to the same durable ledger.

        V2 keeps the existing host-control admission and SQLite job event chain,
        but binds an ordered, multi-resource plan in the admission baseline.
        Each resource has its own durable intent and exact readback record. A
        crash between them leaves the admission at that pending step; callers
        cannot skip it or replay the plan.
        """
        from ._w26_os_resources import ResourcePolicyError

        event_contracts = {
            "APPLY_STEP_INTENT": ("APPLY", {"step_index", "action_id", "resource_kind", "resource_key",
                                                  "desired_identity_sha256", "dependency_sha256"}),
            "APPLY_STEP_READBACK_VERIFIED": ("APPLY", {"step_index", "action_id", "resource_kind", "resource_key",
                                                         "desired_identity_sha256", "created_resource_sha256",
                                                         "dependency_sha256"}),
            "RESTORE_STEP_INTENT": ("RESTORE", {"step_index", "action_id", "resource_kind", "resource_key",
                                                    "expected_resource_sha256", "dependency_sha256"}),
            "RESTORE_STEP_READBACK_VERIFIED": ("RESTORE", {"step_index", "action_id", "resource_kind", "resource_key",
                                                              "expected_resource_sha256", "removed_resource_sha256",
                                                              "dependency_sha256"}),
            "RECOVERY_REQUIRED": (None, {"reason_code", "failed_event", "step_index", "action_id",
                                          "resource_kind", "resource_key"}),
        }
        if (not isinstance(admission_id, str) or not admission_id
                or event not in event_contracts or not isinstance(payload, dict)
                or set(payload) != event_contracts[event][1]):
            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                      "V2 mutation event identity, type, or payload is outside the closed contract")
        digest_fields = {"desired_identity_sha256", "created_resource_sha256",
                         "expected_resource_sha256", "removed_resource_sha256", "dependency_sha256"}
        identifiers = {"action_id", "resource_kind", "resource_key", "reason_code", "failed_event"}
        for key, value in payload.items():
            if key in digest_fields and (not isinstance(value, str)
                                         or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                          "V2 mutation event digest is malformed")
            if key == "step_index" and (type(value) is not int or value < 0 or value >= 16):
                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                          "V2 mutation step index is outside its bound")
            if key in identifiers and (not isinstance(value, str) or not value or len(value) > 256
                                       or any(ord(char) < 32 for char in value)):
                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                          "V2 mutation event identifier is malformed")
        encoded_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False)
        if len(encoded_payload.encode("utf-8")) > 16 * 1024:
            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                      "V2 mutation event exceeds its durable bound")

        def canonical(value: Any) -> str:
            return json.dumps(value, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False)

        def digest(value: Any) -> str:
            return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

        event_name = "HostControlMutationJournalV2"
        try:
            with self.lock:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    row = self.db.execute(
                        "SELECT a.*,j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
                        "o.status AS operation_status,o.operation,o.metadata AS operation_metadata "
                        "FROM host_control_admissions a "
                        "JOIN jobs j ON j.job_id=a.job_id AND j.operation_id=a.operation_id "
                        "JOIN operations o ON o.operation_id=a.operation_id "
                        "WHERE a.admission_id=?", (admission_id,),
                    ).fetchone()
                    if row is None:
                        raise ResourcePolicyError("HOST_CONTROL_ADMISSION_UNKNOWN",
                                                  "V2 admission is absent from OperationStore")
                    if self.db.execute(
                            "SELECT 1 FROM job_events WHERE job_id=? AND event=? LIMIT 1",
                            (row["job_id"], "HostControlMutationJournalV1"),
                    ).fetchone() is not None:
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                                                  "V1 and V2 host mutation journals cannot be mixed")
                    try:
                        root_identity = json.loads(row["root_identity_json"])
                        job_metadata = json.loads(row["job_metadata"] or "{}")
                        operation_metadata = json.loads(row["operation_metadata"] or "{}")
                        baseline = json.loads(row["baseline_json"])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ResourcePolicyError("HOST_CONTROL_BINDING_UNKNOWN",
                                                  "V2 admission scope or metadata is malformed") from exc
                    binding = {
                        "schema": "comsol-mcp.internal-host-control-operation/1",
                        "admission_id": admission_id,
                        "phase": row["phase"], "project_id": row["project_id"],
                        "host_id": row["host_id"], "root_identity": root_identity,
                        "profile_sha256": row["profile_sha256"], "plan_sha256": row["plan_sha256"],
                        "source_plan_sha256": row["source_plan_sha256"],
                        "source_receipt_sha256": row["source_receipt_sha256"],
                    }
                    for metadata in (job_metadata, operation_metadata):
                        if (metadata.get("host_control") != binding
                                or metadata.get("project_id") != row["project_id"]
                                or metadata.get("host_id") != row["host_id"]
                                or metadata.get("project_root_identity") != root_identity.get("file_id")
                                or metadata.get("project_root_volume_id") != root_identity.get("volume_id")
                                or metadata.get("project_root_owner_id") != root_identity.get("owner_id")):
                            raise ResourcePolicyError("HOST_CONTROL_BINDING_MISMATCH",
                                                      "V2 job and operation no longer match the exact admitted host scope")
                    if (row["operation"] != f"host.change.{str(row['phase']).lower()}"
                            or row["phase"] not in {"APPLY", "RESTORE"}
                            or row["job_status"] != "RUNNING" or row["operation_status"] != "RUNNING"):
                        raise ResourcePolicyError("HOST_CONTROL_STATE_CONFLICT",
                                                  "V2 mutation events require the exact admitted running operation")
                    if (not isinstance(baseline, dict)
                            or hashlib.sha256(canonical(baseline).encode("utf-8")).hexdigest()
                                != row["baseline_sha256"]):
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                  "V2 baseline hash does not match its durable admission")
                    sequence = baseline.get("mutation_sequence")
                    if (not isinstance(sequence, dict)
                            or set(sequence) != {"schema", "phase", "plan_sha256", "steps"}
                            or sequence.get("schema") not in {"comsol-mcp.host-control-mutation-sequence/2", "comsol-mcp.host-control-mutation-sequence/3", "comsol-mcp.host-control-mutation-sequence/4", "comsol-mcp.host-control-mutation-sequence/5", "comsol-mcp.host-control-mutation-sequence/6"}
                            or sequence.get("phase") != row["phase"]
                            or sequence.get("plan_sha256") != row["plan_sha256"]
                            or not isinstance(sequence.get("steps"), list)
                            or not (1 <= len(sequence["steps"]) <= 3 if sequence.get("schema") == "comsol-mcp.host-control-mutation-sequence/2" else (6 <= len(sequence["steps"]) <= 4098 if sequence.get("schema") == "comsol-mcp.host-control-mutation-sequence/3" else len(sequence["steps"]) == 1))):
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                  "V2 admission lacks an exact bounded mutation sequence")
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/3":
                        from ._windows_filter_group import validate_journal_group
                        validate_journal_group(baseline, dict(row))
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/4":
                        from ._windows_service_sid import validate_journal_leaf
                        validate_journal_leaf(baseline, dict(row))
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/5":
                        from ._windows_appcontainer import validate_journal_leaf as validate_appcontainer_leaf
                        validate_appcontainer_leaf(baseline, dict(row))
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/6":
                        from ._windows_appcontainer import validate_restore_journal_leaf
                        validate_restore_journal_leaf(baseline, dict(row))
                    step_fields = {"step_index", "action_id", "resource_kind", "resource_key",
                                   "identity_sha256", "dependency_sha256", "expected_resource_sha256"}
                    expected_phase = str(row["phase"])
                    action_ids: set[str] = set()
                    resource_keys: set[str] = set()
                    step_kinds: list[str] = []
                    dependency_digests: set[str] = set()
                    for index, step in enumerate(sequence["steps"]):
                        if (not isinstance(step, dict) or set(step) != step_fields
                                or step.get("step_index") != index or type(step.get("step_index")) is not int
                                or not isinstance(step.get("action_id"), str) or not step["action_id"]
                                or step.get("resource_kind") not in ({"appcontainer_profile"} if sequence["schema"] in {"comsol-mcp.host-control-mutation-sequence/5", "comsol-mcp.host-control-mutation-sequence/6"} else {"windows_service"} if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/4" else {"wfp_provider", "wfp_sublayer", "wfp_filter"})
                                or not isinstance(step.get("resource_key"), str) or not step["resource_key"]
                                or any(not isinstance(step.get(key), str)
                                       or re.fullmatch(r"[0-9a-f]{64}", step[key]) is None
                                       for key in ("identity_sha256", "dependency_sha256"))
                                or (expected_phase == "APPLY" and step.get("expected_resource_sha256") is not None)
                                or (expected_phase == "RESTORE"
                                    and (not isinstance(step.get("expected_resource_sha256"), str)
                                         or re.fullmatch(r"[0-9a-f]{64}",
                                                         step["expected_resource_sha256"]) is None))):
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 admitted mutation step is malformed")
                        try:
                            parsed_key = "{" + str(UUID(step["resource_key"].strip("{}"))).upper() + "}"
                        except (AttributeError, TypeError, ValueError) as exc:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 resource key is not a GUID") from exc
                        if parsed_key != step["resource_key"]:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 resource key is not canonically encoded")
                        if step["action_id"] in action_ids or parsed_key in resource_keys:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 sequence duplicates an action or resource key")
                        action_ids.add(step["action_id"])
                        resource_keys.add(parsed_key)
                        step_kinds.append(step["resource_kind"])
                        dependency_digests.add(step["dependency_sha256"])
                    apply_orders = (["wfp_provider", "wfp_sublayer"],
                                    ["wfp_provider", "wfp_sublayer", "wfp_filter"])
                    allowed_orders = (apply_orders if expected_phase == "APPLY"
                                      else tuple(list(reversed(order)) for order in apply_orders))
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/3":
                        order = ["wfp_provider", "wfp_sublayer"] + ["wfp_filter"] * (len(sequence["steps"]) - 2)
                        allowed_orders = [order if expected_phase == "APPLY" else list(reversed(order))]
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/4":
                        allowed_orders = [["windows_service"]]
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/5":
                        allowed_orders = [["appcontainer_profile"]] if expected_phase == "APPLY" else []
                    if sequence["schema"] == "comsol-mcp.host-control-mutation-sequence/6":
                        allowed_orders = [["appcontainer_profile"]] if expected_phase == "RESTORE" else []
                    if step_kinds not in allowed_orders:
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                  "V2 plan must use the exact provider/sublayer/filter dependency order")
                    if len(dependency_digests) != 1:
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                  "V2 sequence does not share one exact dependency digest")
                    records = self.db.execute(
                        "SELECT id,metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id",
                        (row["job_id"], event_name),
                    ).fetchall()
                    intent_name = f"{expected_phase}_STEP_INTENT"
                    verified_name = f"{expected_phase}_STEP_READBACK_VERIFIED"
                    previous = "0" * 64
                    state = "ADMITTED"
                    next_index = 0
                    pending: dict[str, Any] | None = None
                    for ordinal, record_row in enumerate(records):
                        try:
                            prior = json.loads(record_row["metadata"])
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 journal row is malformed") from exc
                        expected_record_fields = {"schema", "admission_id", "job_id", "operation_id", "project_id",
                                                  "host_id", "root_identity", "profile_sha256", "plan_sha256",
                                                  "source_plan_sha256", "source_receipt_sha256", "phase", "sequence",
                                                  "previous_sha256", "event", "payload", "record_sha256"}
                        if (not isinstance(prior, dict) or set(prior) != expected_record_fields
                                or prior.get("schema") != "comsol-mcp.host-control-mutation-journal/2"
                                or prior.get("sequence") != ordinal or type(prior.get("sequence")) is not int
                                or prior.get("previous_sha256") != previous
                                or prior.get("admission_id") != admission_id
                                or prior.get("job_id") != row["job_id"]
                                or prior.get("operation_id") != row["operation_id"]
                                or prior.get("project_id") != row["project_id"]
                                or prior.get("host_id") != row["host_id"]
                                or prior.get("root_identity") != root_identity
                                or prior.get("profile_sha256") != row["profile_sha256"]
                                or prior.get("plan_sha256") != row["plan_sha256"]
                                or prior.get("source_plan_sha256") != row["source_plan_sha256"]
                                or prior.get("source_receipt_sha256") != row["source_receipt_sha256"]
                                or prior.get("phase") != expected_phase
                                or not isinstance(prior.get("payload"), dict)
                                or not isinstance(prior.get("record_sha256"), str)):
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 journal chain scope is invalid")
                        body = {key: value for key, value in prior.items() if key != "record_sha256"}
                        if digest(body) != prior["record_sha256"]:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 journal record hash is invalid")
                        old_event, old_payload = prior["event"], prior["payload"]
                        if state == "RECOVERY_REQUIRED":
                            raise ResourcePolicyError(
                                "HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                                "V2 recovery fence is absorbing; a later ordinary journal event is not permitted",
                            )
                        if old_event == "RECOVERY_REQUIRED":
                            expected_keys = event_contracts[old_event][1]
                            if (set(old_payload) != expected_keys or pending is None
                                    or old_payload.get("failed_event") != intent_name
                                    or old_payload.get("step_index") != pending.get("step_index")
                                    or old_payload.get("action_id") != pending.get("action_id")
                                    or old_payload.get("resource_kind") != pending.get("resource_kind")
                                    or old_payload.get("resource_key") != pending.get("resource_key")):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "V2 recovery marker does not bind the pending step")
                            state = "RECOVERY_REQUIRED"
                        elif old_event == intent_name:
                            if pending is not None or next_index >= len(sequence["steps"]):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "V2 journal duplicated or exceeded the admitted action order")
                            step = sequence["steps"][next_index]
                            fields = event_contracts[intent_name][1]
                            if (set(old_payload) != fields or old_payload.get("step_index") != next_index
                                    or old_payload.get("action_id") != step["action_id"]
                                    or old_payload.get("resource_kind") != step["resource_kind"]
                                    or old_payload.get("resource_key") != step["resource_key"]
                                    or old_payload.get("dependency_sha256") != step["dependency_sha256"]
                                    or (expected_phase == "APPLY"
                                        and old_payload.get("desired_identity_sha256") != step["identity_sha256"])
                                    or (expected_phase == "RESTORE"
                                        and old_payload.get("expected_resource_sha256") != step["expected_resource_sha256"])):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "V2 intent differs from the exact admitted ordered action")
                            pending = dict(old_payload)
                            state = f"{expected_phase}_STEP_INTENT:{next_index}"
                        elif old_event == verified_name:
                            if pending is None:
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "V2 readback has no preceding durable intent")
                            fields = event_contracts[verified_name][1]
                            if any(old_payload.get(key) != pending.get(key)
                                   for key in fields - {"created_resource_sha256", "removed_resource_sha256"}):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "V2 readback differs from its exact pending intent")
                            observed_resource = old_payload.get(
                                "created_resource_sha256" if expected_phase == "APPLY" else "removed_resource_sha256")
                            if (set(old_payload) != fields
                                    or not isinstance(observed_resource, str)
                                    or re.fullmatch(r"[0-9a-f]{64}", observed_resource) is None
                                    or (expected_phase == "RESTORE"
                                        and observed_resource != pending.get("expected_resource_sha256"))):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                          "V2 readback does not prove the exact resource effect")
                            pending = None
                            next_index += 1
                            state = ("APPLIED" if expected_phase == "APPLY" else "RESTORED") \
                                if next_index == len(sequence["steps"]) else f"{expected_phase}_STEP_VERIFIED:{next_index - 1}"
                        else:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                      "V2 journal contains an unknown event")
                        previous = prior["record_sha256"]
                    if state != row["state"]:
                        raise ResourcePolicyError("HOST_CONTROL_JOURNAL_CORRUPT",
                                                  "V2 admission state differs from its journal history")
                    if state == "RECOVERY_REQUIRED":
                        raise ResourcePolicyError(
                            "HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                            "V2 recovery fence is absorbing; ordinary append cannot clear it",
                        )
                    phase_for_event = event_contracts[event][0]
                    if event == "RECOVERY_REQUIRED":
                        if (state == "RECOVERY_REQUIRED" or pending is None
                                or payload.get("failed_event") != intent_name
                                or payload.get("step_index") != pending.get("step_index")
                                or payload.get("action_id") != pending.get("action_id")
                                or payload.get("resource_kind") != pending.get("resource_kind")
                                or payload.get("resource_key") != pending.get("resource_key")):
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                                                      "V2 recovery event must identify the active durable intent")
                        new_state = "RECOVERY_REQUIRED"
                    else:
                        if phase_for_event != expected_phase:
                            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                                                      "V2 event phase differs from the admitted operation")
                        step_index = payload["step_index"]
                        if event == intent_name:
                            if pending is not None or step_index != next_index or step_index >= len(sequence["steps"]):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                                                          "V2 intent cannot skip, overlap, or replay a step")
                            step = sequence["steps"][step_index]
                            if (payload.get("action_id") != step["action_id"]
                                    or payload.get("resource_kind") != step["resource_kind"]
                                    or payload.get("resource_key") != step["resource_key"]
                                    or payload.get("dependency_sha256") != step["dependency_sha256"]
                                    or (expected_phase == "APPLY"
                                        and payload.get("desired_identity_sha256") != step["identity_sha256"])
                                    or (expected_phase == "RESTORE"
                                        and payload.get("expected_resource_sha256") != step["expected_resource_sha256"])):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                                          "V2 intent differs from the admitted action sequence")
                            new_state = f"{expected_phase}_STEP_INTENT:{step_index}"
                        else:
                            if pending is None:
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_STATE_CONFLICT",
                                                          "V2 readback has no active durable intent")
                            fields = event_contracts[event][1]
                            if (set(payload) != fields
                                    or any(payload.get(key) != pending.get(key)
                                           for key in fields - {"created_resource_sha256", "removed_resource_sha256"})
                                    or (expected_phase == "RESTORE"
                                        and payload.get("removed_resource_sha256")
                                            != pending.get("expected_resource_sha256"))):
                                raise ResourcePolicyError("HOST_CONTROL_JOURNAL_INVALID",
                                                          "V2 readback does not match the exact durable intent")
                            new_state = ("APPLIED" if expected_phase == "APPLY" else "RESTORED") \
                                if next_index + 1 == len(sequence["steps"]) else \
                                f"{expected_phase}_STEP_VERIFIED:{next_index}"
                    body = {
                        "schema": "comsol-mcp.host-control-mutation-journal/2",
                        "admission_id": admission_id, "job_id": row["job_id"],
                        "operation_id": row["operation_id"], "project_id": row["project_id"],
                        "host_id": row["host_id"], "root_identity": root_identity,
                        "profile_sha256": row["profile_sha256"], "plan_sha256": row["plan_sha256"],
                        "source_plan_sha256": row["source_plan_sha256"],
                        "source_receipt_sha256": row["source_receipt_sha256"],
                        "phase": row["phase"], "sequence": len(records),
                        "previous_sha256": previous, "event": event, "payload": payload,
                    }
                    record = {**body, "record_sha256": digest(body)}
                    self.db.execute(
                        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                        (row["job_id"], event_name, canonical(record)),
                    )
                    updated = self.db.execute(
                        "UPDATE host_control_admissions SET state=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE admission_id=? AND state=?",
                        (new_state, admission_id, state),
                    )
                    if updated.rowcount != 1:
                        raise ResourcePolicyError("HOST_CONTROL_STATE_CONFLICT",
                                                  "V2 admission state changed while appending its event")
                    self.db.execute("COMMIT")
                except BaseException:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise
                row = self.db.execute(
                    "SELECT id,metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id DESC LIMIT 1",
                    (row["job_id"], event_name),
                ).fetchone()
                admission = self.db.execute(
                    "SELECT state FROM host_control_admissions WHERE admission_id=?", (admission_id,)
                ).fetchone()
                if (row is None or json.loads(row["metadata"]) != record
                        or admission is None or admission["state"] != new_state):
                    raise ResourcePolicyError("HOST_CONTROL_JOURNAL_READBACK_UNKNOWN",
                                              "committed V2 mutation event failed exact readback")
                return {"id": int(row["id"]), **record, "state": new_state}
        except ResourcePolicyError:
            raise
        except Exception as exc:
            raise ResourcePolicyError("HOST_CONTROL_JOURNAL_UNKNOWN",
                                      "V2 mutation journal transaction failed closed",
                                      details={"cause_type": type(exc).__name__}) from exc

    def record_appcontainer_delete_api_return(self, attempt: Any) -> dict[str, Any]:
        """Idempotently journal one exact original AppContainer Delete S_OK.

        This is a diagnostic acknowledgment only. It never advances the
        mutation journal to RESTORED or claims a postimage observation.
        """
        from ._w26_os_resources import ResourcePolicyError
        from ._windows_appcontainer_lifecycle import (
            ProfileRestoreAttempt, _RESTORE_ATTEMPT_SEAL,
        )
        from ._windows_appcontainer_native import _NativeDeleteAttempt

        if (type(attempt) is not ProfileRestoreAttempt
                or attempt._seal is not _RESTORE_ATTEMPT_SEAL
                or type(attempt.native_attempt) is not _NativeDeleteAttempt
                or attempt.native_attempt.state != "API_CONFIRMED"
                or attempt.native_attempt.hresult != 0
                or not attempt.native_attempt.entered or not attempt.native_attempt.finished):
            raise ResourcePolicyError(
                "HOST_CONTROL_APPCONTAINER_DELETE_RETURN_UNKNOWN",
                "only the exact captured original signed HRESULT S_OK can be acknowledged",
            )
        selection = attempt.selection
        effect = selection.effect
        admission = attempt.admission
        native_attempt = attempt.native_attempt
        if (effect._restore_attempt is not attempt
                or native_attempt.effect is not effect
                or native_attempt.api is not effect.api
                or admission is None
                or selection.executor.store is not self
                or not effect._is_rooted()
                or selection.action.get("resource_kind") != "appcontainer_profile"
                or selection.action.get("resource_key") != selection.restore_body.get("resource_key")):
            raise ResourcePolicyError(
                "HOST_CONTROL_APPCONTAINER_DELETE_OWNER_UNKNOWN",
                "Delete acknowledgment does not match its original effect/admission root",
            )

        def canonical(value: Any) -> str:
            return json.dumps(value, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False)

        def digest(value: Any) -> str:
            return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

        event_name = "AppContainerProfileDeleteApiReturnCaptured"
        try:
            with self.lock:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    row = self.db.execute(
                        "SELECT a.*,j.status AS job_status,j.metadata AS job_metadata,"
                        "o.status AS operation_status,o.operation,o.metadata AS operation_metadata "
                        "FROM host_control_admissions a "
                        "JOIN jobs j ON j.job_id=a.job_id AND j.operation_id=a.operation_id "
                        "JOIN operations o ON o.operation_id=a.operation_id "
                        "WHERE a.admission_id=?",
                        (admission.admission_id,),
                    ).fetchone()
                    if row is None:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_ADMISSION_UNKNOWN",
                            "the original AppContainer restore admission is absent",
                        )
                    try:
                        baseline = json.loads(row["baseline_json"])
                        root_identity = json.loads(row["root_identity_json"])
                        job_metadata = json.loads(row["job_metadata"] or "{}")
                        operation_metadata = json.loads(row["operation_metadata"] or "{}")
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_JOURNAL_CORRUPT",
                            "AppContainer restore admission metadata is malformed",
                        ) from exc
                    expected_rows = {
                        "admission_id": admission.admission_id,
                        "operation_id": admission.operation_id,
                        "job_id": admission.job_id,
                        "phase": "RESTORE",
                        "project_id": selection.request.project_id,
                        "host_id": effect.profile.host_id,
                        "profile_sha256": effect.profile.sha256,
                        "plan_sha256": selection.restore_sha256,
                        "source_plan_sha256": selection.plan_sha256,
                        "source_receipt_sha256": selection.receipt_sha256,
                        "authorization_ref_sha256": attempt.authorization_ref_sha256,
                    }
                    if (any(row[key] != value for key, value in expected_rows.items())
                            or row["operation"] != "host.change.restore"
                            or row["job_status"] not in {"RUNNING", "UNKNOWN"}
                            or row["operation_status"] not in {"RUNNING", "UNKNOWN"}
                            or row["state"] not in {"RESTORE_STEP_INTENT:0", "RECOVERY_REQUIRED"}
                            or digest(baseline) != row["baseline_sha256"]):
                        raise ResourcePolicyError(
                            "HOST_CONTROL_ADMISSION_MISMATCH",
                            "the exact original AppContainer RESTORE scope/status is not current",
                        )
                    from ._windows_appcontainer import validate_restore_journal_leaf
                    validate_restore_journal_leaf(baseline, dict(row))
                    binding = {
                        "schema": "comsol-mcp.internal-host-control-operation/1",
                        "admission_id": row["admission_id"],
                        "phase": row["phase"],
                        "project_id": row["project_id"],
                        "host_id": row["host_id"],
                        "root_identity": root_identity,
                        "profile_sha256": row["profile_sha256"],
                        "plan_sha256": row["plan_sha256"],
                        "source_plan_sha256": row["source_plan_sha256"],
                        "source_receipt_sha256": row["source_receipt_sha256"],
                    }
                    for metadata in (job_metadata, operation_metadata):
                        if (metadata.get("host_control") != binding
                                or metadata.get("project_id") != row["project_id"]
                                or metadata.get("host_id") != row["host_id"]
                                or metadata.get("project_root_identity") != root_identity.get("file_id")
                                or metadata.get("project_root_volume_id") != root_identity.get("volume_id")
                                or metadata.get("project_root_owner_id") != root_identity.get("owner_id")):
                            raise ResourcePolicyError(
                                "HOST_CONTROL_BINDING_MISMATCH",
                                "restore operation/job no longer match the admitted project/root",
                            )

                    restore_source = baseline["appcontainer_restore"]
                    source = self.db.execute(
                        "SELECT a.*,j.status AS job_status,o.status AS operation_status,o.result "
                        "FROM host_control_admissions a "
                        "JOIN jobs j ON j.job_id=a.job_id AND j.operation_id=a.operation_id "
                        "JOIN operations o ON o.operation_id=a.operation_id "
                        "WHERE a.admission_id=?",
                        (restore_source["source_admission_id"],),
                    ).fetchone()
                    if source is None:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_APPCONTAINER_APPLY_RESULT_UNKNOWN",
                            "the original successful CREATE admission is absent",
                        )
                    try:
                        source_result = json.loads(source["result"])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_APPCONTAINER_APPLY_RESULT_UNKNOWN",
                            "the original successful CREATE result is malformed",
                        ) from exc
                    if (source["phase"] != "APPLY" or source["state"] != "APPLIED"
                            or source["operation_id"] != restore_source["source_operation_id"]
                            or source["job_id"] != restore_source["source_job_id"]
                            or source["operation_status"] != "SUCCEEDED"
                            or source["job_status"] != "SUCCEEDED"
                            or source["plan_sha256"] != row["source_plan_sha256"]
                            or source["profile_sha256"] != row["profile_sha256"]
                            or source["project_id"] != row["project_id"]
                            or source["host_id"] != row["host_id"]
                            or source["root_identity_json"] != row["root_identity_json"]
                            or source["source_plan_sha256"] is not None
                            or source["source_receipt_sha256"] is not None
                            or digest(source_result) != restore_source["source_result_sha256"]
                            or source_result.get("receipt_sha256") != row["source_receipt_sha256"]
                            or source_result.get("applied_receipt", {}).get("receipt_sha256")
                                != row["source_receipt_sha256"]):
                        raise ResourcePolicyError(
                            "HOST_CONTROL_APPCONTAINER_APPLY_RESULT_UNKNOWN",
                            "the original durable CREATE result/journal does not match this restore",
                        )

                    journal_rows = self.db.execute(
                        "SELECT metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id",
                        (row["job_id"], "HostControlMutationJournalV2"),
                    ).fetchall()
                    previous = "0" * 64
                    expected_fields = {
                        "schema", "admission_id", "job_id", "operation_id", "project_id",
                        "host_id", "root_identity", "profile_sha256", "plan_sha256",
                        "source_plan_sha256", "source_receipt_sha256", "phase", "sequence",
                        "previous_sha256", "event", "payload", "record_sha256",
                    }
                    state = "ADMITTED"
                    pending = None
                    if len(journal_rows) not in {1, 2}:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_JOURNAL_CORRUPT",
                            "AppContainer Delete requires exactly one restore intent and optional recovery marker",
                        )
                    for index, journal_row in enumerate(journal_rows):
                        record = json.loads(journal_row["metadata"])
                        if (not isinstance(record, dict) or set(record) != expected_fields
                                or record.get("schema") != "comsol-mcp.host-control-mutation-journal/2"
                                or record.get("sequence") != index
                                or type(record.get("sequence")) is not int
                                or record.get("previous_sha256") != previous
                                or record.get("admission_id") != row["admission_id"]
                                or record.get("job_id") != row["job_id"]
                                or record.get("operation_id") != row["operation_id"]
                                or record.get("project_id") != row["project_id"]
                                or record.get("host_id") != row["host_id"]
                                or record.get("root_identity") != root_identity
                                or record.get("profile_sha256") != row["profile_sha256"]
                                or record.get("plan_sha256") != row["plan_sha256"]
                                or record.get("source_plan_sha256") != row["source_plan_sha256"]
                                or record.get("source_receipt_sha256") != row["source_receipt_sha256"]
                                or record.get("phase") != "RESTORE"
                                or digest({key: value for key, value in record.items()
                                          if key != "record_sha256"}) != record.get("record_sha256")):
                            raise ResourcePolicyError(
                                "HOST_CONTROL_JOURNAL_CORRUPT",
                                "AppContainer restore journal hash-chain binding is invalid",
                            )
                        payload = record.get("payload")
                        if record.get("event") == "RESTORE_STEP_INTENT" and index == 0:
                            step = baseline["mutation_sequence"]["steps"][0]
                            if (set(payload or {}) != {
                                    "step_index", "action_id", "resource_kind", "resource_key",
                                    "expected_resource_sha256", "dependency_sha256"}
                                    or payload.get("step_index") != 0
                                    or payload.get("action_id") != step["action_id"]
                                    or payload.get("resource_kind") != "appcontainer_profile"
                                    or payload.get("resource_key") != step["resource_key"]
                                    or payload.get("expected_resource_sha256")
                                        != step["expected_resource_sha256"]
                                    or payload.get("dependency_sha256") != step["dependency_sha256"]):
                                raise ResourcePolicyError(
                                    "HOST_CONTROL_JOURNAL_CORRUPT",
                                    "AppContainer restore intent differs from its exact leaf binding",
                                )
                            pending = dict(payload)
                            state = "RESTORE_STEP_INTENT:0"
                        elif record.get("event") == "RECOVERY_REQUIRED" and index == 1:
                            if (pending is None or set(payload or {}) != {
                                    "reason_code", "failed_event", "step_index", "action_id",
                                    "resource_kind", "resource_key"}
                                    or payload.get("failed_event") != "RESTORE_STEP_INTENT"
                                    or payload.get("step_index") != 0
                                    or payload.get("action_id") != pending["action_id"]
                                    or payload.get("resource_kind") != pending["resource_kind"]
                                    or payload.get("resource_key") != pending["resource_key"]):
                                raise ResourcePolicyError(
                                    "HOST_CONTROL_JOURNAL_CORRUPT",
                                    "AppContainer recovery marker does not bind its pending Delete intent",
                                )
                            state = "RECOVERY_REQUIRED"
                        else:
                            raise ResourcePolicyError(
                                "HOST_CONTROL_JOURNAL_CORRUPT",
                                "API-confirmed Delete cannot be recorded after a restore readback event",
                            )
                        previous = record["record_sha256"]
                    if state != row["state"]:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_JOURNAL_CORRUPT",
                            "AppContainer restore journal and admission state differ",
                        )

                    action = selection.action
                    payload = {
                        "schema": "comsol-mcp.appcontainer-profile-delete-api-return/1",
                        "attempt_id": attempt.attempt_id,
                        "operation_id": row["operation_id"],
                        "job_id": row["job_id"],
                        "admission_id": row["admission_id"],
                        "source_operation_id": source["operation_id"],
                        "source_job_id": source["job_id"],
                        "source_admission_id": source["admission_id"],
                        "plan_sha256": row["plan_sha256"],
                        "source_plan_sha256": row["source_plan_sha256"],
                        "source_receipt_sha256": row["source_receipt_sha256"],
                        "profile_sha256": row["profile_sha256"],
                        "resource_kind": "appcontainer_profile",
                        "resource_key": action["resource_key"],
                        "expected_created_resource_sha256":
                            selection.expected_resource_sha256,
                        "authorization_ref_sha256": row["authorization_ref_sha256"],
                        "api_name": "DeleteAppContainerProfile",
                        "return_type": "HRESULT_S32",
                        "hresult": 0,
                        "native_delete_state": "API_CONFIRMED",
                        "postimage_state": "UNVERIFIED",
                        "restore_state": "POSTIMAGE_UNVERIFIED",
                        "safe_retry": False,
                        "evidence_scope": "NATIVE_API_RETURN_ONLY",
                    }
                    existing_rows = self.db.execute(
                        "SELECT id,metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id",
                        (row["job_id"], event_name),
                    ).fetchall()
                    if len(existing_rows) > 1:
                        raise ResourcePolicyError(
                            "HOST_CONTROL_APPCONTAINER_DELETE_ACK_CONFLICT",
                            "duplicate Delete API-return acknowledgments are not permitted",
                        )
                    if existing_rows:
                        existing = json.loads(existing_rows[0]["metadata"])
                        body = {key: value for key, value in existing.items()
                                if key != "record_sha256"}
                        if (not isinstance(existing, dict)
                                or existing.get("schema")
                                    != "comsol-mcp.appcontainer-delete-api-return-event/1"
                                or existing.get("attempt_id") != attempt.attempt_id
                                or existing.get("sequence") != 0
                                or existing.get("previous_sha256") != "0" * 64
                                or existing.get("payload") != payload
                                or digest(body) != existing.get("record_sha256")):
                            raise ResourcePolicyError(
                                "HOST_CONTROL_APPCONTAINER_DELETE_ACK_CONFLICT",
                                "cached Delete API-return acknowledgment differs from the original attempt",
                            )
                        self.db.execute("COMMIT")
                        return {"state": "CACHED", "event_id": int(existing_rows[0]["id"]),
                                "attempt_id": attempt.attempt_id, "payload": payload}
                    body = {
                        "schema": "comsol-mcp.appcontainer-delete-api-return-event/1",
                        "attempt_id": attempt.attempt_id,
                        "sequence": 0,
                        "previous_sha256": "0" * 64,
                        "payload": payload,
                    }
                    record = {**body, "record_sha256": digest(body)}
                    self.db.execute(
                        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                        (row["job_id"], event_name, canonical(record)),
                    )
                    event_row = self.db.execute(
                        "SELECT id,metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id DESC LIMIT 1",
                        (row["job_id"], event_name),
                    ).fetchone()
                    if (event_row is None or json.loads(event_row["metadata"]) != record):
                        raise ResourcePolicyError(
                            "HOST_CONTROL_APPCONTAINER_DELETE_ACK_UNKNOWN",
                            "Delete API-return acknowledgment failed exact durable readback",
                        )
                    self.db.execute("COMMIT")
                    return {"state": "RECORDED", "event_id": int(event_row["id"]),
                            "attempt_id": attempt.attempt_id, "payload": payload}
                except BaseException:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise
        except ResourcePolicyError:
            raise
        except Exception as exc:
            raise ResourcePolicyError(
                "HOST_CONTROL_APPCONTAINER_DELETE_ACK_UNKNOWN",
                "Delete API-return acknowledgment failed closed",
                details={"cause_type": type(exc).__name__},
            ) from exc

    def host_control_admission_state(self, admission_id: str) -> str:
        """Read the exact durable state of one internal host admission."""
        from ._w26_os_resources import ResourcePolicyError

        if not isinstance(admission_id, str) or not admission_id:
            raise ResourcePolicyError("HOST_CONTROL_ADMISSION_INVALID",
                                      "host admission ID is malformed")
        with self.lock:
            row = self.db.execute(
                "SELECT state FROM host_control_admissions WHERE admission_id=?",
                (admission_id,),
            ).fetchone()
        if row is None or not isinstance(row["state"], str):
            raise ResourcePolicyError("HOST_CONTROL_ADMISSION_UNKNOWN",
                                      "host admission state could not be read")
        return row["state"]

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

    def finish(self, operation_id: str, *, status: str, result: dict[str, Any], _lifecycle_update=None) -> tuple[bool, str]:
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
                if _lifecycle_update is not None:
                    session_id, updater = _lifecycle_update
                    self._update_session_lifecycle_in_transaction(session_id, updater)
                self.db.execute("COMMIT")
                return True, status
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def record_stage_claim_failure(
        self, job_id: str, operation_id: str, claim_id: str, result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Record a failed stage claim without replacing another owner's result.

        A still-queued job may be fenced before another callback claims it. A
        running job may be fenced only by the invocation whose claim ID was
        stored in the same transaction as its QUEUED-to-RUNNING transition.
        Existing results and other states remain authoritative.
        """
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT j.status AS job_status,j.metadata AS job_metadata,"
                    "o.status AS operation_status,o.result,o.operation "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                    "WHERE j.job_id=? AND o.operation_id=?",
                    (job_id, operation_id),
                ).fetchone()
                if row is None or row["operation"] not in {"experiment.stage_define", "experiment.stage_run"}:
                    self.db.execute("COMMIT")
                    return None
                if row["result"] is not None:
                    authoritative = json.loads(row["result"])
                    self.db.execute("COMMIT")
                    return authoritative
                job_status = row["job_status"]
                metadata = json.loads(row["job_metadata"] or "{}")
                owns_running = (job_status == "RUNNING"
                                and metadata.get("stage_callback_claim_id") == claim_id)
                if row["operation_status"] != job_status or not (job_status == "QUEUED" or owns_running):
                    self.db.execute("COMMIT")
                    return None
                self.db.execute("UPDATE jobs SET status='UNKNOWN' WHERE job_id=?", (job_id,))
                self.db.execute(
                    "UPDATE operations SET status='UNKNOWN',result=? WHERE operation_id=?",
                    (_dumps_canonical(result), operation_id),
                )
                self.db.execute(
                    "INSERT INTO job_events(job_id,event,metadata) VALUES(?, 'UNKNOWN', ?)",
                    (job_id, _dumps_canonical({"operation_id": operation_id, "at": time.time()})),
                )
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
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

    def job_for_status_wait(
        self, job_id: str, *, max_inline_result_bytes: int,
    ) -> dict[str, Any] | None:
        """Read status/wait data without decoding an oversized stored result.

        The result byte count and job row are read in one SQLite snapshot. Small
        results use the existing ``_job`` projection unchanged. For a result
        above the inline budget, this returns the job row plus an explicit
        ``job.result`` retrieval hint, without selecting or JSON-decoding
        ``operations.result`` in Python. SQLite may still need to scan or
        temporarily convert the stored TEXT for ``length(CAST(... AS BLOB))``;
        this is not a whole-process memory bound.
        """
        if type(max_inline_result_bytes) is not int or max_inline_result_bytes < 0:
            raise ValueError("max_inline_result_bytes must be a non-negative integer")
        with self.lock:
            started_transaction = not self.db.in_transaction
            if started_transaction:
                self.db.execute("BEGIN")
            try:
                row = self.db.execute(
                    "SELECT j.*,o.operation AS _status_wait_operation,"
                    "length(CAST(o.result AS BLOB)) AS _status_wait_result_bytes "
                    "FROM jobs j LEFT JOIN operations o ON o.operation_id=j.operation_id "
                    "WHERE j.job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    value = None
                else:
                    result_bytes = row["_status_wait_result_bytes"]
                    if result_bytes is None or result_bytes <= max_inline_result_bytes:
                        job_row = self.db.execute(
                            "SELECT * FROM jobs WHERE job_id=?", (job_id,),
                        ).fetchone()
                        value = self._job(job_row) if job_row else None
                    else:
                        value = {
                            key: row[key] for key in row.keys()
                            if key not in {
                                "_status_wait_operation", "_status_wait_result_bytes",
                            }
                        }
                        value["metadata"] = json.loads(value["metadata"] or "{}")
                        value["effective_timeouts"] = json.loads(
                            value.get("effective_timeouts") or "{}"
                        )
                        # The public status schema defines operation as a name;
                        # the legacy _job projection additionally nests the full
                        # operation/result record for inline-sized results.
                        value["operation"] = row["_status_wait_operation"]
                        value["result_omitted"] = True
                        value["result_size_bytes"] = int(result_bytes)
                        value["result_available_via"] = "job.result"
                if started_transaction and self.db.in_transaction:
                    self.db.execute("COMMIT")
                return value
            except BaseException:
                if started_transaction and self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

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
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    @contextmanager
    def project_job_read_snapshot(
        self, job_id: str, project_id: str,
    ) -> Iterator[bool]:
        """Hold one SQLite read snapshot while checking a project binding (T35).

        Additive store-consistency primitive: not authentication, not a public
        MCP operation, and nothing here is wired into the daemon. The daemon
        must authorize separately before entering this context.

        Under the store RLock this starts a SQLite read transaction (unless
        the caller already owns one, which is never silently cleared) and
        issues exactly one metadata-only query joining the exact job to its
        associated operation by their stored operation_id. Both durable
        metadata values must parse and yield the same unambiguous project ID
        through the pure ``consistent_project_id`` helper, and that value must
        equal the exact, non-normalized ``project_id`` argument. Any other
        outcome — job not found, missing operation binding, missing or
        malformed JSON, absent or ambiguous project claims, or a different
        project — yields ``False`` with no result, content, or job-event
        query performed.

        While ``True`` is yielded, the caller may perform its immediate
        synchronous protected ``store.job``/``store.events`` reads; they
        reacquire the RLock on the same thread and observe this same
        snapshot. The read transaction is concluded when the context exits,
        including when the caller raises. Transaction setup or cleanup
        failures propagate instead of masquerading as a successful protected
        read. Only a metadata decode error on the selected row — a JSON
        syntax error, a non-string payload type, or a payload that is not
        valid UTF-8 — classifies that row as unbound; operational SQLite
        errors are not swallowed. This supports
        coherent same-snapshot visibility only: it does not prove metadata
        immutability, future revocation behaviour, or any principal identity.
        """
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("project_job_read_snapshot requires a non-empty job_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_job_read_snapshot requires a non-empty project_id")
        with self.lock:
            started_transaction = not self.db.in_transaction
            if started_transaction:
                self.db.execute("BEGIN")
            try:
                row = self.db.execute(
                    "SELECT j.job_id,j.metadata AS job_metadata,o.metadata AS operation_metadata "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                    "WHERE j.job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    bound = False
                else:
                    try:
                        job_metadata = json.loads(row["job_metadata"] or "{}")
                        operation_metadata = json.loads(row["operation_metadata"] or "{}")
                    except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
                        # Only this invalid row is unbound; operational DB
                        # errors must propagate, not hide as a denial.
                        bound = False
                    else:
                        bound = (
                            consistent_project_id(job_metadata, operation_metadata) == project_id
                        )
                yield bound
                if started_transaction and self.db.in_transaction:
                    self.db.execute("COMMIT")
            except BaseException:
                if started_transaction and self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    @contextmanager
    def job_log_read_snapshot(
        self, job_id: str, project_id: str | None, *,
        max_binding_bytes: int,
    ) -> Iterator[bool]:
        """Hold one SQLite read snapshot for the bounded ``job.log`` path (T35).

        Additive store-consistency primitive backing the daemon's bounded
        ``job.log`` reads: it is not authentication, not a public MCP
        operation, and nothing here establishes a principal — ``project_root``
        or untrusted argument fields never create a binding, only the pure
        ``consistent_project_id`` helper over the durable job and operation
        metadata can certify one. The ``operations.result`` column is never
        selected or touched.

        Transaction ownership mirrors ``project_job_read_snapshot``: the store
        RLock plus exactly one SQLite read transaction is held. If the caller
        already owns a transaction it is never taken over, committed or
        rolled back here; only a transaction this context started is closed,
        and that ownership holds even when the caller raises ``BaseException``.
        Transaction setup or cleanup failures propagate instead of
        masquerading as a denial, and SQLite ``OperationalError`` from these
        queries is never swallowed into an authorization ``False``.

        With ``project_id=None`` this is existence-only: it selects just the
        ``jobs`` row for the ``job_id`` and yields a bool without reading or
        decoding any metadata. An empty-string ``job_id`` yields ``False`` in
        this branch; a non-string ``job_id`` is rejected by the caller (and
        refused here with ``ValueError`` as a defensive guard).

        With a non-``None`` ``project_id``, the project ID must be a non-empty
        string and the ``job_id`` a non-empty string, and the exact job is
        joined to its associated operation by stored ``operation_id`` in the
        same transaction with the same consistency semantics as
        ``project_job_read_snapshot``. First only
        ``length(CAST(metadata AS BLOB))`` scalars are read for both sides: a
        missing job or operation row, a NULL length, or a combined size above
        the caller-supplied positive-integer ``max_binding_bytes`` certifies
        nothing, and the context yields ``False`` without fetching payloads.
        Only within budget are the two exact metadata values re-read in this
        same snapshot and decoded; malformed JSON, a non-string payload, or
        invalid UTF-8 likewise yields ``False``; otherwise the binding holds
        exactly when ``consistent_project_id(...) == project_id`` for the
        exact, non-normalized argument. This byte limit applies only to this
        new path; ``project_job_read_snapshot`` itself is unchanged.
        """
        if not isinstance(job_id, str):
            raise ValueError("job_log_read_snapshot requires a string job_id")
        if project_id is not None and (not isinstance(project_id, str) or not project_id):
            raise ValueError(
                "job_log_read_snapshot requires a non-empty project_id or None"
            )
        if type(max_binding_bytes) is not int or max_binding_bytes <= 0:
            raise ValueError("max_binding_bytes must be a positive integer")
        if project_id is not None and not job_id:
            raise ValueError(
                "job_log_read_snapshot requires a non-empty job_id when a project binding is checked"
            )
        with self.lock:
            started_transaction = not self.db.in_transaction
            if started_transaction:
                self.db.execute("BEGIN")
            try:
                if project_id is None:
                    bound = bool(job_id) and self.db.execute(
                        "SELECT 1 FROM jobs WHERE job_id=? LIMIT 1", (job_id,),
                    ).fetchone() is not None
                else:
                    probe = self.db.execute(
                        "SELECT length(CAST(j.metadata AS BLOB)) AS job_metadata_bytes,"
                        "length(CAST(o.metadata AS BLOB)) AS operation_metadata_bytes "
                        "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                        "WHERE j.job_id=?",
                        (job_id,),
                    ).fetchone()
                    if (probe is None
                            or probe["job_metadata_bytes"] is None
                            or probe["operation_metadata_bytes"] is None
                            or int(probe["job_metadata_bytes"])
                            + int(probe["operation_metadata_bytes"]) > max_binding_bytes):
                        bound = False
                    else:
                        row = self.db.execute(
                            "SELECT j.metadata AS job_metadata,o.metadata AS operation_metadata "
                            "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                            "WHERE j.job_id=?",
                            (job_id,),
                        ).fetchone()
                        if row is None:
                            bound = False
                        else:
                            try:
                                job_metadata = json.loads(row["job_metadata"] or "{}")
                                operation_metadata = json.loads(row["operation_metadata"] or "{}")
                            except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
                                # Only this invalid row is unbound; operational
                                # DB errors propagate, never as a denial.
                                bound = False
                            else:
                                bound = (
                                    consistent_project_id(job_metadata, operation_metadata)
                                    == project_id
                                )
                yield bound
                if started_transaction and self.db.in_transaction:
                    self.db.execute("COMMIT")
            except BaseException:
                if started_transaction and self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def list_jobs(
        self,
        offset: int = 0,
        limit: int = 100,
        status: str | None = None,
        project_id: str | None = None,
        *,
        enforce_project_binding: bool = False,
    ) -> JobList:
        bound_offset = max(0, int(offset))
        bound_limit = max(1, min(int(limit), 1000))
        if enforce_project_binding is True:
            return self._list_jobs_project_bound(bound_offset, bound_limit, status, project_id)
        if enforce_project_binding is not False:
            raise TypeError("enforce_project_binding must be a boolean")
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

    def _list_jobs_project_bound(
        self, bound_offset: int, bound_limit: int,
        status: str | None, project_id: str | None,
    ) -> JobList:
        """Strict project-binding job page (T35): one same-snapshot filtered set.

        Additive strict mode reached only through
        ``list_jobs(..., enforce_project_binding=True)``; the legacy unscoped
        SQL branch above is untouched.  Requires a non-empty exact
        ``project_id`` string (never normalized).  Inside one ``BEGIN`` read
        transaction under the store RLock, only the metadata columns of each
        job joined to its associated operation are scanned, in the existing
        ``rowid`` DESC order with the optional status filter applied in SQL
        first.  A job is included only when the pure ``consistent_project_id``
        helper returns exactly ``project_id`` for the independently parsed
        durable job and operation metadata; rows with a missing operation
        binding or missing, malformed, divergent or ambiguous metadata are
        excluded, and ``project_root`` or untrusted argument fields never
        create a match.  ``total``, the page, and ``has_more`` all derive
        from this one filtered set before offset/limit, so no page is
        partially filled by filtering afterwards and no protected result JSON
        of an excluded entry is ever materialized.  Page jobs are materialized
        inside the same snapshot through the existing ``self.job``/``_job``
        semantics.  The scan consumes SQLite rows in fixed ``fetchmany``
        batches, counts every matching row toward ``total``, and retains
        only the matching job IDs inside the requested ``offset``/``limit``
        page window — never the skipped matching prefix — so Python-side ID
        retention is O(limit); the scan itself is O(total job rows) because
        ``total`` must come from the same snapshot before paging.
        Row-by-row metadata payload decoding, the materialized page result
        content, and SQLite-internal buffers are separate concerns that this
        does not bound, and this is a consistency/visibility guarantee, not
        an authentication claim or a bound on whole-process memory.
        """
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("enforce_project_binding requires a non-empty string project_id")
        scan_batch = 256
        with self.lock:
            started_transaction = not self.db.in_transaction
            if started_transaction:
                self.db.execute("BEGIN")
            try:
                if status is not None:
                    cursor = self.db.execute(
                        "SELECT j.job_id,j.metadata AS job_metadata,o.metadata AS operation_metadata "
                        "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                        "WHERE j.status=? ORDER BY j.rowid DESC",
                        (str(status),),
                    )
                else:
                    cursor = self.db.execute(
                        "SELECT j.job_id,j.metadata AS job_metadata,o.metadata AS operation_metadata "
                        "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                        "ORDER BY j.rowid DESC",
                    )
                page_window = bound_offset + bound_limit
                matched_job_ids: list[str] = []
                total = 0
                while True:
                    batch = cursor.fetchmany(scan_batch)
                    if not batch:
                        break
                    for row in batch:
                        try:
                            job_metadata = json.loads(row["job_metadata"] or "{}")
                            operation_metadata = json.loads(row["operation_metadata"] or "{}")
                        except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if consistent_project_id(job_metadata, operation_metadata) != project_id:
                            continue
                        # ``total`` is the zero-based index of this match,
                        # so only matches inside the requested page window
                        # are retained and the skipped prefix accumulates
                        # nothing.
                        if bound_offset <= total < page_window:
                            matched_job_ids.append(row["job_id"])
                        total += 1
                items: list[dict[str, Any]] = []
                for job_id in matched_job_ids:
                    job = self.job(job_id)
                    if job is None:
                        raise RuntimeError(
                            "project-bound job row vanished inside its own read snapshot"
                        )
                    items.append(job)
                if started_transaction and self.db.in_transaction:
                    self.db.execute("COMMIT")
                return JobList(items, total=total, offset=bound_offset, limit=bound_limit)
            except BaseException:
                if started_transaction and self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def cancel_queued(
        self,
        job_id: str,
        reason: str = "cancelled by user",
        *,
        project_id: str | None = None,
        enforce_project_binding: bool = False,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Cancel a QUEUED job; optional strict project binding (T35, proposal).

        The legacy two-positional form (``job_id``, ``reason``) is unchanged:
        with ``enforce_project_binding`` left at its ``False`` default the
        method behaves exactly as before and ``project_id`` is ignored.
        ``enforce_project_binding=True`` is the only strict mode; any other
        non-False value raises ``TypeError`` (the adjacent ``list_jobs``
        convention) and a strict call without a non-empty string
        ``project_id`` raises ``ValueError``, both before any transaction is
        opened.  In strict mode, inside the same ``BEGIN IMMEDIATE`` write
        transaction and before any status early return, job-record
        materialization, UPDATE or event insert, the durable job row and its
        uniquely associated operation row must independently name the SAME
        unambiguous project ID through the pure ``consistent_project_id``
        helper, and that ID must equal the exact non-normalized
        ``project_id`` argument.  A missing operation association, missing or
        malformed JSON metadata, or a helper verdict of ``None`` rolls back
        and returns ``(False, "PROJECT_BINDING_UNKNOWN", None)``; a binding
        naming a different project rolls back and returns
        ``(False, "PROJECT_SCOPE_MISMATCH", None)``.  ``arguments.project_id``
        and ``project_root`` are never consulted.  A passing binding changes
        nothing else: the CANCELLED / non-QUEUED early returns, the QUEUED
        double-rowcount guard, the event write and the post-commit exception
        guard keep their original behavior and returns.  These codes are an
        internal proposed-level store consistency primitive only: not an
        authentication result, no daemon/scoped routing is wired here, and
        no project authority is granted.
        """
        enforce_strict = enforce_project_binding is True
        if not enforce_strict and enforce_project_binding is not False:
            raise TypeError("enforce_project_binding must be a boolean")
        if enforce_strict and (not isinstance(project_id, str) or not project_id):
            raise ValueError(
                "enforce_project_binding requires a non-empty string project_id"
            )
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row:
                    self.db.execute("ROLLBACK")
                    return False, "NOT_FOUND", None
                current_status = row["status"]
                operation_id = row["operation_id"]
                if enforce_strict:
                    # Scoped verdict precedes every status early return,
                    # self._job(), the UPDATEs and the event insert.
                    operation_row = self.db.execute(
                        "SELECT metadata FROM operations WHERE operation_id=?",
                        (operation_id,),
                    ).fetchone()
                    if operation_row is None:
                        self.db.execute("ROLLBACK")
                        return False, "PROJECT_BINDING_UNKNOWN", None
                    try:
                        job_metadata = json.loads(row["metadata"] or "{}")
                        operation_metadata = json.loads(operation_row["metadata"] or "{}")
                    except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
                        self.db.execute("ROLLBACK")
                        return False, "PROJECT_BINDING_UNKNOWN", None
                    binding = consistent_project_id(job_metadata, operation_metadata)
                    if binding is None:
                        self.db.execute("ROLLBACK")
                        return False, "PROJECT_BINDING_UNKNOWN", None
                    if binding != project_id:
                        self.db.execute("ROLLBACK")
                        return False, "PROJECT_SCOPE_MISMATCH", None
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
                job_updated = self.db.execute(
                    "UPDATE jobs SET status='CANCELLED', finished_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='QUEUED'",
                    (job_id,),
                )
                operation_updated = self.db.execute(
                    "UPDATE operations SET status='CANCELLED', result=?, finished_at=CURRENT_TIMESTAMP WHERE operation_id=? AND status='QUEUED'",
                    (serialized_result, operation_id),
                )
                if job_updated.rowcount != 1 or operation_updated.rowcount != 1:
                    # Row-level status drift (for example a QUEUED job whose
                    # operation row already moved to RUNNING): one guarded
                    # UPDATE matched and the other matched zero rows.  Undo
                    # both updates inside this same transaction before any
                    # CANCELLED event is written, so no split job/operation
                    # state or partial write can commit.
                    self.db.execute("ROLLBACK")
                    return False, "STATE_MISMATCH", None
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
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def record_cancel_intent(
        self,
        job_id: str,
        *,
        project_id: str,
        expected_status: str,
        reason: str,
    ) -> tuple[bool, str]:
        """Record a cancel intent on one bound non-QUEUED job in one write txn (T35).

        Additive durable-consistency store primitive for the daemon's RUNNING
        intent path; it is not authentication, not a public MCP operation,
        grants no project authority, and wires nothing by itself.  Exactly one
        ``BEGIN IMMEDIATE`` transaction reads the job row and its uniquely
        associated operation row, decodes both durable metadata columns, and
        requires the pure ``consistent_project_id`` helper to report the SAME
        unambiguous project ID on both sides, equal to the exact
        non-normalized ``project_id`` argument; ``arguments.project_id`` and
        ``project_root`` are never consulted.  That binding verdict precedes
        every status early return, UPDATE and event insert.  A missing job
        returns ``(False, "NOT_FOUND")``; a missing operation association,
        missing/malformed JSON metadata, or a helper verdict of ``None``
        returns ``(False, "PROJECT_BINDING_UNKNOWN")``; a binding naming a
        different project returns ``(False, "PROJECT_SCOPE_MISMATCH")``.
        Durable job/operation status disagreement returns
        ``(False, "STATE_MISMATCH")``; a current status that differs from the
        pre-read ``expected_status``, or that is QUEUED (owned by
        ``cancel_queued``) or terminal (immutable per D03), returns
        ``(False, "STATUS_CHANGED")``.  Every failure rolls back, so no event
        or metadata change survives, and no job record is ever returned.
        A successful call keeps the status unchanged, merges
        ``cancel_requested=True`` and ``cancel_reason=reason`` into the job
        metadata, reuses ``update_job``'s status/started/finished semantics
        and its no-result branch (the durable ``operations.result`` is never
        overwritten), inserts one ``CancelRequested`` event whose metadata
        fields match the existing ``_cancel_job`` intent, and commits only
        after both guarded UPDATEs affected exactly one row each; an
        unexpected rowcount rolls the whole transaction back as
        ``STATE_MISMATCH``.  No read follows the COMMIT.
        """
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("record_cancel_intent requires a non-empty string project_id")
        if not isinstance(expected_status, str) or not expected_status:
            raise ValueError("record_cancel_intent requires a non-empty string expected_status")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    self.db.execute("ROLLBACK")
                    return False, "NOT_FOUND"
                job_status = row["status"]
                operation_id = row["operation_id"]
                operation_row = self.db.execute(
                    "SELECT status, metadata FROM operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if operation_row is None:
                    self.db.execute("ROLLBACK")
                    return False, "PROJECT_BINDING_UNKNOWN"
                try:
                    job_metadata = json.loads(row["metadata"] or "{}")
                    operation_metadata = json.loads(operation_row["metadata"] or "{}")
                except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
                    self.db.execute("ROLLBACK")
                    return False, "PROJECT_BINDING_UNKNOWN"
                binding = consistent_project_id(job_metadata, operation_metadata)
                if binding is None:
                    self.db.execute("ROLLBACK")
                    return False, "PROJECT_BINDING_UNKNOWN"
                if binding != project_id:
                    self.db.execute("ROLLBACK")
                    return False, "PROJECT_SCOPE_MISMATCH"
                if operation_row["status"] != job_status:
                    self.db.execute("ROLLBACK")
                    return False, "STATE_MISMATCH"
                if (
                    job_status != expected_status
                    or job_status == "QUEUED"
                    or job_status in TERMINAL
                ):
                    self.db.execute("ROLLBACK")
                    return False, "STATUS_CHANGED"

                merged_metadata = {
                    **job_metadata,
                    "cancel_requested": True,
                    "cancel_reason": reason,
                }
                # Same status/started/finished semantics as update_job, with
                # the pre-read status as an atomic guard so in-transaction
                # drift cannot slip through between the two UPDATEs.
                job_updated = self.db.execute(
                    "UPDATE jobs SET status=?,metadata=?,"
                    "started_at=CASE WHEN ? IN ('STARTING','RUNNING') THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END,"
                    "finished_at=CASE WHEN ? IN ('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST') "
                    "THEN COALESCE(finished_at,CURRENT_TIMESTAMP) ELSE finished_at END "
                    "WHERE job_id=? AND status=?",
                    (
                        job_status,
                        _dumps_canonical(merged_metadata),
                        job_status,
                        job_status,
                        job_id,
                        expected_status,
                    ),
                )
                # update_job's no-result branch verbatim: operations.result
                # is never written by an intent record.
                operation_updated = self.db.execute(
                    "UPDATE operations SET status=?,"
                    "started_at=(SELECT started_at FROM jobs WHERE job_id=?),"
                    "finished_at=(SELECT finished_at FROM jobs WHERE job_id=?) "
                    "WHERE operation_id=? AND status=?",
                    (job_status, job_id, job_id, operation_id, expected_status),
                )
                if job_updated.rowcount != 1 or operation_updated.rowcount != 1:
                    # Guarded-row drift: one UPDATE matched and the other did
                    # not.  Undo both inside this transaction so no partial
                    # metadata/timestamp commit and no CancelRequested event
                    # can be observed.
                    self.db.execute("ROLLBACK")
                    return False, "STATE_MISMATCH"
                self.db.execute(
                    "INSERT INTO job_events(job_id, event, metadata) VALUES(?, 'CancelRequested', ?)",
                    (job_id, _dumps_canonical({
                        "reason": reason,
                        "mode": "UNSUPPORTED_NATIVE_CANCEL",
                        "cancel_accepted": True,
                        "engine_stopped": False,
                        "at": time.time(),
                    })),
                )
                self.db.execute("COMMIT")
                return True, "INTENT_RECORDED"
            except Exception:
                if self.db.in_transaction:
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

    def _abandoned_stop_history_snapshot(
        self, project_id: str, session_id: str, recovery_job_id: str,
    ) -> tuple[int, str] | None:
        """Hash the exact session history while the caller holds the SQLite write transaction."""
        rows = self.db.execute(
            "SELECT j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
            "o.operation,o.status AS operation_status,o.request_id,o.idempotency_key,o.request_hash,"
            "o.result AS operation_result FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
            "WHERE (json_extract(COALESCE(j.metadata,'{}'),'$.project_id')=? "
            "OR json_extract(COALESCE(j.metadata,'{}'),'$.execution.project_id')=? "
            "OR json_extract(COALESCE(j.metadata,'{}'),'$.project_root')=?) ORDER BY j.rowid DESC",
            (project_id, project_id, project_id),
        ).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["job_metadata"] or "{}")
            if not isinstance(metadata, dict):
                continue
            bindings = [metadata.get("runtime_binding"), metadata,
                        metadata.get("execution"), metadata.get("arguments")]
            if not any(isinstance(value, dict) and value.get("project_id") == project_id
                       and value.get("session_id") == session_id for value in bindings):
                continue
            try:
                result = json.loads(row["operation_result"]) if row["operation_result"] else None
            except (TypeError, json.JSONDecodeError):
                result = None
            jobs.append({
                "job_id": row["job_id"], "operation_id": row["operation_id"],
                "status": row["job_status"], "metadata": metadata, "result": result,
                "operation": {
                    "operation": row["operation"], "status": row["operation_status"],
                    "request_id": row["request_id"], "idempotency_key": row["idempotency_key"],
                    "request_hash": row["request_hash"],
                },
            })
        positions = [index for index, item in enumerate(jobs) if item["job_id"] == recovery_job_id]
        if len(positions) != 1:
            return None
        history = jobs[positions[0] + 1:]
        allowed = {"session.start", "session.connect", "session.disconnect", "session.stop",
                   "session.recover", "session.inspect", "session.health"}
        source_stop_count = 0
        for item in history:
            operation = item["operation"]
            name = operation.get("operation")
            if name not in allowed:
                return None
            if name == "session.stop":
                source_stop_count += 1
                if (item["status"] not in {"UNKNOWN", "RECONCILING"}
                        or operation.get("status") != item["status"]):
                    return None
            elif (item["status"] not in TERMINAL or operation.get("status") not in TERMINAL):
                return None
        if source_stop_count != 1:
            return None
        snapshot = []
        for item in history:
            operation = item["operation"]
            snapshot.append({
                "job_id": item["job_id"], "operation_id": item["operation_id"],
                "operation": operation.get("operation"),
                "metadata_sha256": session_recovery_evidence_sha256({"metadata": {
                    key: value for key, value in item["metadata"].items()
                    if key not in {"reconciled_quiescent", "session_lifecycle_recovery_resolution"}
                }}),
                "result_sha256": session_recovery_evidence_sha256({"result": item["result"]}),
                "status": operation.get("status"),
                "request_id": operation.get("request_id"),
                "idempotency_key": operation.get("idempotency_key"),
                "request_hash": operation.get("request_hash"),
            })
        return len(history), session_recovery_evidence_sha256({"session_history": snapshot})

    def mark_interrupted_session_start_unknown(
        self, *, job_id: str, operation_id: str, request_id: str,
        idempotency_key: str, request_hash: str, project_id: str,
        session_id: str, unknown_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Conservatively reconcile one exact interrupted start into UNKNOWN.

        This is deliberately limited to a source-bound ``session.start`` row.
        The lifecycle transition, result/status update, and append-only event
        commit together. Existing UNKNOWN results are preserved verbatim.
        """
        identities = (job_id, operation_id, request_id, idempotency_key,
                      request_hash, project_id, session_id)
        if (not all(isinstance(value, str) and value for value in identities)
                or not isinstance(unknown_result, dict)):
            raise ValueError("interrupted session.start reconciliation requires exact identities")

        from ._session_lifecycle import validate_lifecycle_record

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
                    "o.request_id,o.idempotency_key,o.request_hash,o.operation,"
                    "o.status AS operation_status,o.metadata AS operation_metadata,o.result AS operation_result "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SOURCE_JOB_NOT_FOUND", "job": None}
                if (row["operation_id"] != operation_id or row["request_id"] != request_id
                        or row["idempotency_key"] != idempotency_key
                        or row["request_hash"] != request_hash
                        or row["operation"] != "session.start"):
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SOURCE_REQUEST_IDENTITY_MISMATCH", "job": None}
                job_metadata = json.loads(row["job_metadata"] or "{}")
                operation_metadata = json.loads(row["operation_metadata"] or "{}")
                if (not isinstance(job_metadata, dict) or not isinstance(operation_metadata, dict)
                        or job_metadata.get("project_id") != project_id
                        or job_metadata.get("session_id") != session_id
                        or job_metadata.get("operation") not in {None, "session.start"}
                        or operation_metadata.get("project_id") != project_id
                        or operation_metadata.get("session_id") != session_id
                        or operation_metadata.get("runtime_id") != job_metadata.get("runtime_id")):
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SOURCE_SESSION_BINDING_MISMATCH", "job": None}
                allowed = {"RUNNING", "STARTING", "RECONCILING", "UNKNOWN"}
                if row["job_status"] not in allowed or row["operation_status"] not in allowed:
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SOURCE_NOT_INTERRUPTED", "job": None}

                session_row = self.db.execute(
                    "SELECT metadata FROM sessions WHERE session_id=?", (session_id,),
                ).fetchone()
                if session_row is None:
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SESSION_LIFECYCLE_NOT_FOUND", "job": None}
                session_metadata = json.loads(session_row["metadata"] or "{}")
                raw_lifecycle = session_metadata.get("lifecycle") if isinstance(session_metadata, dict) else None
                try:
                    current = validate_lifecycle_record(raw_lifecycle)
                except Exception:
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SESSION_LIFECYCLE_MALFORMED", "job": None}
                if (current["project_id"] != project_id or current["session_id"] != session_id
                        or current["runtime_id"] != operation_metadata.get("runtime_id")
                        or current["state"] not in {"STARTING", "UNKNOWN"}
                        or current["endpoint"] is not None
                        or current["server_process_identity"] is not None
                        or current["worker_instance_id"] is not None
                        or current["worker_epoch"] is not None
                        or current["server_instance_id"] is not None):
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SESSION_LIFECYCLE_NOT_INTERRUPTED_START", "job": None}

                try:
                    existing_result = json.loads(row["operation_result"] or "null")
                except (TypeError, json.JSONDecodeError):
                    existing_result = None
                result = existing_result if isinstance(existing_result, dict) else unknown_result
                result_execution = result.get("execution") if isinstance(result, dict) else None
                if (not isinstance(result_execution, dict)
                        or result_execution.get("request_id") != request_id
                        or result_execution.get("operation_id") != operation_id
                        or result_execution.get("request_hash") != request_hash
                        or result_execution.get("idempotency_key") != idempotency_key
                        or result_execution.get("job_id") != job_id):
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SOURCE_RESULT_IDENTITY_MISMATCH", "job": None}
                if result.get("success") is not False:
                    self.db.execute("ROLLBACK")
                    return {"updated": False, "reason": "SOURCE_RESULT_NOT_UNKNOWN", "job": None}

                changed = False
                old_state = current["state"]
                if old_state == "STARTING":
                    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    updated_lifecycle = dict(current)
                    updated_lifecycle.update({
                        "revision": current["revision"] + 1,
                        "state": "UNKNOWN",
                        "endpoint": None,
                        "client_state": "UNKNOWN",
                        "server_state": "UNKNOWN",
                        "server_ownership": "unknown",
                        "health": {"status": "UNKNOWN", "observed_at": None, "source": None},
                        "updated_at": now,
                    })
                    updated_lifecycle = validate_lifecycle_record(updated_lifecycle)
                    session_metadata["lifecycle"] = updated_lifecycle
                    self.db.execute(
                        "UPDATE sessions SET metadata=? WHERE session_id=?",
                        (_dumps_canonical(session_metadata), session_id),
                    )
                    changed = True

                if row["job_status"] != "UNKNOWN" or row["operation_status"] != "UNKNOWN":
                    self.db.execute(
                        "UPDATE jobs SET status='UNKNOWN' WHERE job_id=?", (job_id,),
                    )
                    self.db.execute(
                        "UPDATE operations SET status='UNKNOWN',result=? WHERE operation_id=?",
                        (_dumps_canonical(result), operation_id),
                    )
                    changed = True
                elif not isinstance(existing_result, dict):
                    self.db.execute(
                        "UPDATE operations SET result=? WHERE operation_id=?",
                        (_dumps_canonical(result), operation_id),
                    )
                    changed = True

                if changed:
                    startup_rows = self.db.execute(
                        "SELECT id,metadata FROM job_events WHERE job_id=? "
                        "AND event='SessionServerStartupObservation' ORDER BY id",
                        (job_id,),
                    ).fetchall()
                    startup_observations = []
                    for startup_row in startup_rows:
                        startup_metadata = json.loads(startup_row["metadata"] or "{}")
                        if (not isinstance(startup_metadata, dict)
                                or startup_metadata.get("job_id") != job_id
                                or startup_metadata.get("operation_id") != operation_id
                                or startup_metadata.get("request_id") != request_id
                                or startup_metadata.get("idempotency_key") != idempotency_key
                                or startup_metadata.get("request_hash") != request_hash
                                or startup_metadata.get("project_id") != project_id
                                or startup_metadata.get("session_id") != session_id
                                or startup_metadata.get("runtime_id") != operation_metadata.get("runtime_id")):
                            self.db.execute("ROLLBACK")
                            return {"updated": False, "reason": "STARTUP_OBSERVATION_BINDING_MISMATCH", "job": None}
                        startup_observations.append({"id": int(startup_row["id"]), "metadata": startup_metadata})
                    self.db.execute(
                        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                        (job_id, "SessionStartInterruptedUnknown", _dumps_canonical({
                            "schema_version": 1,
                            "job_id": job_id,
                            "operation_id": operation_id,
                            "request_id": request_id,
                            "idempotency_key": idempotency_key,
                            "request_hash": request_hash,
                            "project_id": project_id,
                            "session_id": session_id,
                            "runtime_id": operation_metadata.get("runtime_id"),
                            "job_status_before": row["job_status"],
                            "operation_status_before": row["operation_status"],
                            "lifecycle_state_before": old_state,
                            "lifecycle_state_after": "UNKNOWN",
                            "startup_observation_count": len(startup_observations),
                            "startup_observations_sha256": session_recovery_evidence_sha256({
                                "startup_observations": startup_observations,
                            }),
                            "server_birth_observed": "UNKNOWN",
                            "server_exit_code_observed": "UNKNOWN",
                            "replayed": False,
                        })),
                    )
                self.db.execute("COMMIT")
                return {"updated": changed, "reason": None,
                        "job": self.job(job_id), "lifecycle": self.get_metadata("sessions", session_id).get("lifecycle")}
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

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
        if evidence.get("source_operation") == "session.start":
            return self._record_interrupted_session_start_resolution(
                job_id, source_operation_id, expected_lifecycle_revision,
                lifecycle_after, evidence,
            )
        abandoned_stop = evidence.get("resolution_scope") == (
            "ABANDONED_UNKNOWN_STOP_CURRENT_HOST_QUIESCENCE"
        )
        if abandoned_stop:
            required = {
                "schema_version", "source_job_id", "source_operation_id", "source_request_id",
                "source_idempotency_key", "source_request_hash", "session_recovery_operation_id",
                "session_recovery_job_id", "session_recovery_request_id", "session_recovery_idempotency_key",
                "project_id", "session_id", "runtime_id", "source_operation", "source_status",
                "source_operation_status", "source_result_sha256", "original_unknown_reason",
                "original_unknown_cause_type", "prior_start_job_id", "prior_start_operation_id",
                "prior_start_result_sha256", "startup_observations_sha256", "prior_disconnect_job_id",
                "prior_disconnect_operation_id", "prior_disconnect_result_sha256",
                "prior_disconnect_worker_retirement_sha256", "prior_disconnect_worker_close_started_sha256",
                "prior_disconnect_worker_close_reaped_sha256", "prior_disconnect_worker_instance_id",
                "prior_disconnect_worker_epoch", "session_history_job_count",
                "session_history_sha256", "endpoint", "server_process_identity", "server_pid_observation",
                "observed_at_utc", "control_daemon_identity", "process_inventory", "listener_inventory",
                "resolution_scope", "classification", "replay_performed", "new_worker_created",
            }
        else:
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
                or (evidence.get("source_operation") != "session.stop" if abandoned_stop else
                    evidence.get("source_operation") not in {
                        "session.connect", "session.disconnect", "session.reconnect",
                    })
                or evidence.get("source_status") not in {"UNKNOWN", "RECONCILING"}
                or evidence.get("source_operation_status") not in {"UNKNOWN", "RECONCILING"}
                or evidence.get("replay_performed") is not False
                or evidence.get("new_worker_created") is not False
                or not isinstance(evidence.get("classification"), str)
                or not evidence.get("classification")
                or (not abandoned_stop and (
                    not isinstance(evidence.get("worker_observation"), dict)
                    or not isinstance(evidence.get("original_worker_binding"), dict)
                    or not isinstance(evidence.get("request_observations"), list)
                    or not isinstance(evidence.get("terminal_reply_identities"), list)
                    or evidence.get("resolution_scope") not in {
                        "SESSION_LIFECYCLE_RPC_TERMINAL_QUIESCENCE",
                        "SESSION_WORKER_RETIRED_EXACT",
                        "ABANDONED_UNKNOWN_CONNECT_CURRENT_HOST_QUIESCENCE",
                    }
                ))
                or (abandoned_stop and (
                    evidence.get("source_operation") != "session.stop"
                    or evidence.get("source_status") != evidence.get("source_operation_status")
                    or evidence.get("original_unknown_reason") != "EXECUTION_STATE_UNKNOWN"
                    or evidence.get("original_unknown_cause_type") != "OwnedServerError"
                    or evidence.get("resolution_scope") != "ABANDONED_UNKNOWN_STOP_CURRENT_HOST_QUIESCENCE"
                    or evidence.get("classification") != "ABANDONED_UNKNOWN_STOP_CURRENT_HOST_QUIESCENCE"
                    or not all(isinstance(evidence.get(key), str) and evidence[key]
                               for key in ("source_request_id", "source_idempotency_key", "source_request_hash",
                                           "session_recovery_job_id", "session_recovery_request_id",
                                           "session_recovery_idempotency_key", "project_id", "session_id",
                                           "runtime_id", "prior_start_job_id", "prior_start_operation_id",
                                           "prior_disconnect_job_id", "prior_disconnect_operation_id"))
                    or not all(isinstance(evidence.get(key), str) and len(evidence[key]) == 64
                               for key in ("source_request_hash", "source_result_sha256",
                                           "prior_start_result_sha256", "startup_observations_sha256",
                                           "prior_disconnect_result_sha256",
                                           "prior_disconnect_worker_retirement_sha256",
                                           "prior_disconnect_worker_close_started_sha256",
                                           "prior_disconnect_worker_close_reaped_sha256",
                                           "session_history_sha256"))
                    or type(evidence.get("session_history_job_count")) is not int
                    or evidence.get("session_history_job_count", 0) < 4
                    or not isinstance(evidence.get("server_process_identity"), dict)
                    or not isinstance(evidence.get("endpoint"), dict)
                    or evidence.get("server_pid_observation") != {
                        "state": "EXITED_EXACT",
                        "pid": evidence.get("server_process_identity", {}).get("pid"),
                        "birth": evidence.get("server_process_identity", {}).get("birth"),
                    }
                    or not isinstance(evidence.get("observed_at_utc"), str)
                    or not evidence.get("observed_at_utc")
                    or not isinstance(evidence.get("control_daemon_identity"), dict)
                    or not isinstance(evidence.get("process_inventory"), dict)
                    or not isinstance(evidence.get("listener_inventory"), dict)
                ))):
            raise ValueError("lifecycle recovery evidence is incomplete or has mismatched source binding")

        if abandoned_stop and (
                type(evidence.get("prior_disconnect_worker_epoch")) is not int
                or evidence["prior_disconnect_worker_epoch"] < 1
                or not isinstance(evidence.get("prior_disconnect_worker_instance_id"), str)
                or not evidence["prior_disconnect_worker_instance_id"]):
            raise ValueError("abandoned stop worker retirement identity is incomplete")

        abandoned_connect = evidence.get("resolution_scope") == "ABANDONED_UNKNOWN_CONNECT_CURRENT_HOST_QUIESCENCE"
        if abandoned_stop:
            processes, listeners = evidence["process_inventory"], evidence["listener_inventory"]
            control = evidence["control_daemon_identity"]
            if (set(control) != {"pid", "process_start_epoch_ms", "singleton_lock_held"}
                    or type(control.get("pid")) is not int or control["pid"] <= 1
                    or type(control.get("process_start_epoch_ms")) is not int
                    or control["process_start_epoch_ms"] <= 0 or control.get("singleton_lock_held") is not True
                    or set(processes) != {
                        "status", "candidate_count", "unresolved_candidate_count", "task_owned_match_count",
                        "canonical_engine_match_count", "startup_pid_count", "startup_pid_live_count",
                        "startup_pid_unresolved_count",
                    }
                    or processes.get("status") != "COMPLETE"
                    or any(type(processes.get(key)) is not int or processes[key] < 0 for key in (
                        "candidate_count", "unresolved_candidate_count", "task_owned_match_count",
                        "canonical_engine_match_count", "startup_pid_count", "startup_pid_live_count",
                        "startup_pid_unresolved_count",
                    ))
                    or any(processes.get(key) != 0 for key in (
                        "candidate_count", "unresolved_candidate_count", "task_owned_match_count",
                        "canonical_engine_match_count", "startup_pid_live_count", "startup_pid_unresolved_count",
                    )) or processes.get("startup_pid_count", 0) < 1
                    or set(listeners) != {
                        "status", "rows_scanned", "owner_query_unresolved_count",
                        "task_owned_match_count", "canonical_engine_match_count",
                    }
                    or listeners.get("status") != "COMPLETE"
                    or any(type(listeners.get(key)) is not int or listeners[key] < 0 for key in (
                        "rows_scanned", "owner_query_unresolved_count", "task_owned_match_count",
                        "canonical_engine_match_count",
                    ))
                    or any(listeners.get(key) != 0 for key in (
                        "owner_query_unresolved_count", "task_owned_match_count", "canonical_engine_match_count",
                    ))):
                raise ValueError("abandoned stop quiescence evidence is incomplete or unresolved")
        if abandoned_connect:
            processes, listeners = evidence.get("process_inventory"), evidence.get("listener_inventory")
            binding = evidence.get("original_worker_binding")
            requests, replies = evidence.get("request_observations"), evidence.get("terminal_reply_identities")
            process_identity = evidence.get("server_process_identity")
            if (evidence.get("source_operation") != "session.connect"
                    or evidence.get("source_status") not in {"UNKNOWN", "RECONCILING"}
                    or evidence.get("source_operation_status") != evidence.get("source_status")
                    or evidence.get("classification") != "ABANDONED_UNKNOWN_CONNECT_CURRENT_HOST_QUIESCENCE"
                    or not isinstance(processes, dict) or processes.get("status") != "COMPLETE"
                    or any(processes.get(key) != 0 for key in (
                        "candidate_count", "unresolved_candidate_count", "task_owned_match_count",
                        "canonical_engine_match_count", "startup_pid_live_count", "startup_pid_unresolved_count",
                    )) or type(processes.get("startup_pid_count")) is not int or processes["startup_pid_count"] < 1
                    or not isinstance(listeners, dict) or listeners.get("status") != "COMPLETE"
                    or any(listeners.get(key) != 0 for key in (
                        "owner_query_unresolved_count", "task_owned_match_count", "canonical_engine_match_count",
                    ))
                    or not isinstance(binding, dict) or binding.get("kind") != "registered_session"
                    or binding.get("project_id") != evidence.get("project_id")
                    or binding.get("session_id") != evidence.get("session_id")
                    or not isinstance(binding.get("worker_instance_id"), str)
                    or type(binding.get("worker_epoch")) is not int or binding["worker_epoch"] < 1
                    or not isinstance(requests, list) or len(requests) != 1
                    or not isinstance(replies, list) or len(replies) != 1
                    or requests[0].get("status") != "FAILED" or requests[0].get("terminal") is not True
                    or replies[0].get("status") != "FAILED" or replies[0].get("kind") != "connect"
                    or not isinstance(process_identity, dict)
                    or process_identity.get("runtime_id") != evidence.get("runtime_id")
                    or process_identity.get("endpoint") != evidence.get("endpoint")
                    or evidence.get("server_pid_observation") != {
                        "state": "EXITED_EXACT", "pid": process_identity.get("pid"),
                        "birth": process_identity.get("birth"),
                    }
                    or type(evidence.get("session_history_job_count")) is not int
                    or evidence["session_history_job_count"] < 2
                    or any(not isinstance(evidence.get(key), str) or len(evidence[key]) != 64 for key in (
                        "prior_start_result_sha256", "startup_observations_sha256", "session_history_sha256",
                    ))):
                raise ValueError("abandoned connect evidence is incomplete or inconsistent")

        from ._session_lifecycle import validate_lifecycle_record

        proposed = validate_lifecycle_record(lifecycle_after)
        project_id, session_id = evidence["project_id"], evidence["session_id"]
        if (proposed["project_id"] != project_id or proposed["session_id"] != session_id
                or proposed["revision"] != expected_lifecycle_revision + 1
                or proposed["health"] != {"status": "UNKNOWN", "observed_at": None, "source": None}):
            raise ValueError("lifecycle recovery target is not the next exact session revision")
        if (abandoned_connect or abandoned_stop) and (proposed["state"] != "STOPPED"
                or proposed["project_id"] != evidence.get("project_id")
                or proposed["session_id"] != evidence.get("session_id")
                or proposed["runtime_id"] != evidence.get("runtime_id")
                or proposed["client_state"] != "DISCONNECTED" or proposed["server_state"] != "STOPPED"
                or proposed["server_ownership"] != "unknown" or proposed["endpoint"] is not None
                or proposed["worker_instance_id"] is not None or proposed["worker_epoch"] is not None
                or proposed["server_instance_id"] is not None or proposed["server_process_identity"] is not None):
            raise ValueError("abandoned lifecycle recovery may only clear to a non-connected STOPPED lifecycle")

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
                    "o.request_id,o.idempotency_key,o.request_hash,o.status AS operation_status,"
                    "o.operation,o.metadata AS operation_metadata,o.result AS operation_result "
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
                if (abandoned_connect or abandoned_stop) and (
                    row["job_status"] != row["operation_status"]
                    or row["job_status"] != evidence.get("source_status")
                    or row["operation_status"] != evidence.get("source_operation_status")
                ):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_STATUS_BINDING_MISMATCH", "resolution": None}
                if row["operation"] != evidence["source_operation"]:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_OPERATION_KIND_MISMATCH", "resolution": None}
                job_metadata = json.loads(row["job_metadata"] or "{}")
                operation_metadata = json.loads(row["operation_metadata"] or "{}")
                if (not isinstance(job_metadata, dict)
                        or job_metadata.get("project_id") != project_id
                        or job_metadata.get("session_id") != session_id
                        or job_metadata.get("operation") != evidence["source_operation"]
                        or (abandoned_stop and (
                            not isinstance(operation_metadata, dict)
                            or operation_metadata.get("project_id") != project_id
                            or operation_metadata.get("session_id") != session_id
                            or operation_metadata.get("operation") != "session.stop"
                            or row["request_id"] != evidence.get("source_request_id")
                            or row["idempotency_key"] != evidence.get("source_idempotency_key")
                            or row["request_hash"] != evidence.get("source_request_hash")
                        ))):
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
                if abandoned_stop:
                    source_execution = source_result.get("execution")
                    source_error, source_data = source_result.get("error"), source_result.get("data")
                    if (not isinstance(source_execution, dict)
                            or any(source_execution.get(key) != row[key] for key in (
                                "request_id", "idempotency_key", "request_hash",
                            ))
                            or source_execution.get("operation_id") != source_operation_id
                            or source_execution.get("job_id") != job_id
                            or source_result.get("success") is not False
                            or not isinstance(source_error, dict)
                            or source_error.get("code") != "EXECUTION_STATE_UNKNOWN"
                            or source_error.get("execution_state_unknown") is not True
                            or source_error.get("safe_retry") is not False
                            or not isinstance(source_data, dict)
                            or source_data.get("cause_type") != "OwnedServerError"
                            or source_data.get("server_handle_preserved") is not True
                            or source_data.get("server_stopped") is not False
                            or source_data.get("session_id") != session_id):
                        self.db.execute("ROLLBACK")
                        return {"recorded": False, "reason": "ABANDONED_STOP_SOURCE_RESULT_MISMATCH", "resolution": None}
                    recovery = self.db.execute(
                        "SELECT j.job_id,j.status AS job_status,j.metadata AS job_metadata,"
                        "o.request_id,o.idempotency_key,o.operation_id,o.operation,o.status AS operation_status,"
                        "o.metadata AS operation_metadata FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                        "WHERE o.operation_id=?", (evidence["session_recovery_operation_id"],),
                    ).fetchone()
                    if (recovery is None or recovery["job_id"] != evidence["session_recovery_job_id"]
                            or recovery["request_id"] != evidence["session_recovery_request_id"]
                            or recovery["idempotency_key"] != evidence["session_recovery_idempotency_key"]
                            or recovery["operation"] != "session.recover"
                            or recovery["job_status"] not in {"RUNNING", "RECONCILING"}
                            or recovery["operation_status"] not in {"RUNNING", "RECONCILING"}):
                        self.db.execute("ROLLBACK")
                        return {"recorded": False, "reason": "RECOVERY_REQUEST_IDENTITY_MISMATCH", "resolution": None}
                    recovery_job_metadata = json.loads(recovery["job_metadata"] or "{}")
                    recovery_operation_metadata = json.loads(recovery["operation_metadata"] or "{}")
                    if any(not isinstance(value, dict) or value.get("project_id") != project_id
                           or value.get("session_id") != session_id
                           or value.get("operation") != "session.recover"
                           for value in (recovery_job_metadata, recovery_operation_metadata)):
                        self.db.execute("ROLLBACK")
                        return {"recorded": False, "reason": "RECOVERY_SESSION_BINDING_MISMATCH", "resolution": None}
                    history_binding = self._abandoned_stop_history_snapshot(
                        project_id, session_id, evidence["session_recovery_job_id"],
                    )
                    if history_binding != (
                            evidence["session_history_job_count"], evidence["session_history_sha256"]):
                        self.db.execute("ROLLBACK")
                        return {"recorded": False, "reason": "SESSION_HISTORY_CHANGED", "resolution": None}

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
                if abandoned_stop and (
                        current["state"] != "UNKNOWN"
                        or current["runtime_id"] != evidence.get("runtime_id")
                        or current["client_state"] != "RETIRED"
                        or current["server_state"] != "MCP_MANAGED"
                        or current["server_ownership"] != "mcp_managed"
                        or current["endpoint"] != evidence.get("endpoint")
                        or current["server_process_identity"] != evidence.get("server_process_identity")
                        or current["server_instance_id"] is not None
                        or current["worker_instance_id"] != evidence.get("prior_disconnect_worker_instance_id")
                        or current["worker_epoch"] != evidence.get("prior_disconnect_worker_epoch")):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "ABANDONED_STOP_LIFECYCLE_BINDING_MISMATCH", "resolution": None}
                if abandoned_connect and (current["state"] != "UNKNOWN"
                        or current["runtime_id"] != evidence.get("runtime_id")
                        or current["server_ownership"] != "mcp_managed"
                        or current["endpoint"] != evidence.get("endpoint")
                        or current["server_process_identity"] != evidence.get("server_process_identity")):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "ABANDONED_CONNECT_LIFECYCLE_BINDING_MISMATCH", "resolution": None}
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

    def _record_interrupted_session_start_resolution(
        self, job_id: str, source_operation_id: str, expected_lifecycle_revision: int,
        lifecycle_after: dict[str, Any], evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one exact same-host quiescence resolution for a start job."""
        required = {
            "schema_version", "source_job_id", "source_operation_id",
            "source_request_id", "source_idempotency_key", "source_request_hash",
            "session_recovery_operation_id", "session_recovery_job_id",
            "session_recovery_request_id", "session_recovery_idempotency_key",
            "project_id", "session_id", "runtime_id", "source_operation",
            "source_status", "source_operation_status", "source_result_sha256",
            "original_unknown_reason", "startup_observations_sha256",
            "observed_at_utc", "control_daemon_identity", "process_inventory",
            "listener_inventory", "resolution_scope", "classification",
            "replay_performed", "new_worker_created",
        }
        identity_values = (
            evidence.get("source_job_id"), evidence.get("source_operation_id"),
            evidence.get("source_request_id"), evidence.get("source_idempotency_key"),
            evidence.get("source_request_hash"), evidence.get("session_recovery_operation_id"),
            evidence.get("session_recovery_job_id"), evidence.get("session_recovery_request_id"),
            evidence.get("session_recovery_idempotency_key"), evidence.get("project_id"),
            evidence.get("session_id"), evidence.get("runtime_id"),
        )
        control = evidence.get("control_daemon_identity")
        processes = evidence.get("process_inventory")
        listeners = evidence.get("listener_inventory")
        if (not required.issubset(evidence)
                or evidence.get("schema_version") != 1
                or evidence.get("source_job_id") != job_id
                or evidence.get("source_operation_id") != source_operation_id
                or evidence.get("source_operation") != "session.start"
                or evidence.get("source_status") != "UNKNOWN"
                or evidence.get("source_operation_status") != "UNKNOWN"
                or evidence.get("resolution_scope") != "ABANDONED_UNKNOWN_START_CURRENT_HOST_QUIESCENCE"
                or evidence.get("classification") != "ABANDONED_UNKNOWN_START_CURRENT_HOST_QUIESCENCE"
                or evidence.get("replay_performed") is not False
                or evidence.get("new_worker_created") is not False
                or not all(isinstance(value, str) and value for value in identity_values)
                or not isinstance(evidence.get("observed_at_utc"), str)
                or not isinstance(evidence.get("original_unknown_reason"), str)
                or not evidence["original_unknown_reason"]
                or not all(isinstance(evidence.get(key), str) and len(evidence[key]) == 64
                           for key in ("source_result_sha256", "startup_observations_sha256"))
                or not isinstance(control, dict)
                or set(control) != {"pid", "process_start_epoch_ms", "singleton_lock_held"}
                or type(control.get("pid")) is not int or control["pid"] <= 1
                or type(control.get("process_start_epoch_ms")) is not int or control["process_start_epoch_ms"] <= 0
                or control.get("singleton_lock_held") is not True
                or not isinstance(processes, dict)
                or set(processes) != {
                    "status", "candidate_count", "unresolved_candidate_count",
                    "task_owned_match_count", "canonical_engine_match_count",
                    "startup_pid_count", "startup_pid_live_count", "startup_pid_unresolved_count",
                }
                or processes.get("status") != "COMPLETE"
                or any(type(processes.get(key)) is not int or processes[key] < 0 for key in (
                    "candidate_count", "unresolved_candidate_count", "task_owned_match_count",
                    "canonical_engine_match_count", "startup_pid_count", "startup_pid_live_count",
                    "startup_pid_unresolved_count",
                ))
                or any(processes.get(key) != 0 for key in (
                    "unresolved_candidate_count", "task_owned_match_count", "canonical_engine_match_count",
                    "startup_pid_live_count", "startup_pid_unresolved_count",
                ))
                or not isinstance(listeners, dict)
                or set(listeners) != {
                    "status", "rows_scanned", "owner_query_unresolved_count",
                    "task_owned_match_count", "canonical_engine_match_count",
                }
                or listeners.get("status") != "COMPLETE"
                or any(type(listeners.get(key)) is not int or listeners[key] < 0 for key in (
                    "rows_scanned", "owner_query_unresolved_count", "task_owned_match_count",
                    "canonical_engine_match_count",
                ))
                or any(listeners.get(key) != 0 for key in (
                    "owner_query_unresolved_count", "task_owned_match_count", "canonical_engine_match_count",
                ))):
            raise ValueError("session.start quiescence evidence is incomplete or has mismatched source binding")

        from ._session_lifecycle import validate_lifecycle_record

        proposed = validate_lifecycle_record(lifecycle_after)
        project_id, session_id = evidence["project_id"], evidence["session_id"]
        if (proposed["project_id"] != project_id or proposed["session_id"] != session_id
                or proposed["revision"] != expected_lifecycle_revision + 1
                or proposed["state"] != "STOPPED" or proposed["endpoint"] is not None
                or proposed["client_state"] != "DISCONNECTED"
                or proposed["server_state"] != "STOPPED"
                or proposed["server_ownership"] != "unknown"
                or proposed["server_process_identity"] is not None
                or proposed["health"] != {"status": "UNKNOWN", "observed_at": None, "source": None}):
            raise ValueError("session.start quiescence target is not a stopped unknown-ownership lifecycle")

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
                    "o.request_id,o.idempotency_key,o.request_hash,o.status AS operation_status,"
                    "o.operation,o.metadata AS operation_metadata,o.result AS operation_result "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_JOB_NOT_FOUND", "resolution": None}
                if (row["operation_id"] != source_operation_id
                        or row["request_id"] != evidence["source_request_id"]
                        or row["idempotency_key"] != evidence["source_idempotency_key"]
                        or row["request_hash"] != evidence["source_request_hash"]):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_REQUEST_IDENTITY_MISMATCH", "resolution": None}
                existing = self.db.execute(
                    "SELECT id,event,metadata,created_at FROM job_events "
                    "WHERE job_id=? AND event='SessionLifecycleRecoveryResolution' ORDER BY id LIMIT 1",
                    (job_id,),
                ).fetchone()
                if existing is not None:
                    resolution = {**dict(existing), "metadata": json.loads(existing["metadata"] or "{}")}
                    self.db.execute("COMMIT")
                    return {"recorded": False, "reason": "ALREADY_RESOLVED", "resolution": resolution}
                if row["job_status"] != "UNKNOWN" or row["operation_status"] != "UNKNOWN":
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_NOT_UNRESOLVED", "resolution": None}
                if row["operation"] != "session.start":
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_OPERATION_KIND_MISMATCH", "resolution": None}
                job_metadata = json.loads(row["job_metadata"] or "{}")
                operation_metadata = json.loads(row["operation_metadata"] or "{}")
                if (not isinstance(job_metadata, dict) or not isinstance(operation_metadata, dict)
                        or job_metadata.get("project_id") != project_id
                        or job_metadata.get("session_id") != session_id
                        or job_metadata.get("runtime_id") != evidence["runtime_id"]
                        or operation_metadata.get("project_id") != project_id
                        or operation_metadata.get("session_id") != session_id
                        or operation_metadata.get("runtime_id") != evidence["runtime_id"]):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_SESSION_BINDING_MISMATCH", "resolution": None}
                try:
                    source_result = json.loads(row["operation_result"] or "null")
                except (TypeError, json.JSONDecodeError):
                    source_result = None
                if not isinstance(source_result, dict):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_RESULT_UNAVAILABLE", "resolution": None}
                if (session_recovery_evidence_sha256({"source_result": source_result})
                        != evidence["source_result_sha256"]):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_RESULT_CHANGED", "resolution": None}
                source_execution = source_result.get("execution")
                if (not isinstance(source_execution, dict)
                        or source_execution.get("request_id") != evidence["source_request_id"]
                        or source_execution.get("operation_id") != source_operation_id
                        or source_execution.get("request_hash") != evidence["source_request_hash"]
                        or source_execution.get("idempotency_key") != evidence["source_idempotency_key"]
                        or source_execution.get("job_id") != job_id
                        or source_result.get("success") is not False):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_RESULT_IDENTITY_MISMATCH", "resolution": None}

                recovery = self.db.execute(
                    "SELECT j.job_id,j.status AS job_status,j.metadata AS job_metadata,"
                    "o.request_id,o.idempotency_key,o.operation_id,o.operation,o.status AS operation_status,"
                    "o.metadata AS operation_metadata FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                    "WHERE o.operation_id=?",
                    (evidence["session_recovery_operation_id"],),
                ).fetchone()
                if (recovery is None or recovery["job_id"] != evidence["session_recovery_job_id"]
                        or recovery["request_id"] != evidence["session_recovery_request_id"]
                        or recovery["idempotency_key"] != evidence["session_recovery_idempotency_key"]
                        or recovery["operation"] != "session.recover"
                        or recovery["operation_status"] not in {"RUNNING", "RECONCILING"}
                        or recovery["job_status"] not in {"RUNNING", "RECONCILING"}):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "RECOVERY_REQUEST_IDENTITY_MISMATCH", "resolution": None}
                recovery_job_metadata = json.loads(recovery["job_metadata"] or "{}")
                recovery_operation_metadata = json.loads(recovery["operation_metadata"] or "{}")
                if any(not isinstance(value, dict) or value.get("project_id") != project_id
                       or value.get("session_id") != session_id
                       for value in (recovery_job_metadata, recovery_operation_metadata)):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "RECOVERY_SESSION_BINDING_MISMATCH", "resolution": None}

                startup_rows = self.db.execute(
                    "SELECT id,metadata FROM job_events WHERE job_id=? "
                    "AND event='SessionServerStartupObservation' ORDER BY id",
                    (job_id,),
                ).fetchall()
                startup_observations = []
                for startup_row in startup_rows:
                    startup_metadata = json.loads(startup_row["metadata"] or "{}")
                    if (not isinstance(startup_metadata, dict)
                            or startup_metadata.get("job_id") != job_id
                            or startup_metadata.get("operation_id") != source_operation_id
                            or startup_metadata.get("request_id") != evidence["source_request_id"]
                            or startup_metadata.get("idempotency_key") != evidence["source_idempotency_key"]
                            or startup_metadata.get("request_hash") != evidence["source_request_hash"]
                            or startup_metadata.get("project_id") != project_id
                            or startup_metadata.get("session_id") != session_id
                            or startup_metadata.get("runtime_id") != evidence["runtime_id"]):
                        self.db.execute("ROLLBACK")
                        return {"recorded": False, "reason": "STARTUP_OBSERVATION_BINDING_MISMATCH", "resolution": None}
                    startup_observations.append({"id": int(startup_row["id"]), "metadata": startup_metadata})
                if (session_recovery_evidence_sha256({"startup_observations": startup_observations})
                        != evidence["startup_observations_sha256"]):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "STARTUP_OBSERVATIONS_CHANGED", "resolution": None}

                session_row = self.db.execute(
                    "SELECT metadata FROM sessions WHERE session_id=?", (session_id,),
                ).fetchone()
                if session_row is None:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_NOT_FOUND", "resolution": None}
                session_metadata = json.loads(session_row["metadata"] or "{}")
                raw_lifecycle = session_metadata.get("lifecycle") if isinstance(session_metadata, dict) else None
                if not isinstance(raw_lifecycle, dict):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_MALFORMED", "resolution": None}
                current = validate_lifecycle_record(raw_lifecycle)
                if (current["project_id"] != project_id or current["session_id"] != session_id
                        or current["runtime_id"] != evidence["runtime_id"]):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_IDENTITY_MISMATCH", "resolution": None}
                if (current["endpoint"] is not None
                        or current["server_process_identity"] is not None
                        or current["worker_instance_id"] is not None
                        or current["worker_epoch"] is not None
                        or current["server_instance_id"] is not None):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_IDENTITY_PRESENT", "resolution": None}
                if current["revision"] != expected_lifecycle_revision or current["state"] != "UNKNOWN":
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_REVISION_CONFLICT",
                            "current_revision": current["revision"], "current_state": current["state"],
                            "resolution": None}

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
                self.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?",
                                (_dumps_canonical(job_metadata), job_id))
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

    def job_log_page_bounded(
        self, job_id: str, offset: int, limit: int, *,
        max_response_bytes: int,
    ) -> list[dict[str, Any]]:
        """Return one byte-budgeted page of one exact job's events (T35).

        Additive internal read primitive for the daemon's bounded ``job.log``
        response; it changes no old method and never writes, reorders,
        repairs or skips events. It joins a snapshot owned elsewhere: an
        active caller-owned transaction is required under the store RLock
        (``ValueError`` otherwise) and no BEGIN, COMMIT or ROLLBACK is issued
        here. ``offset`` is a non-negative integer and ``limit`` an integer
        from 1 through 1000 ids per call; every bound is caller-supplied and
        validated, never hardcoded from examples.

        Rows are read only for exactly this ``job_id`` in ``ORDER BY id``
        order over the selected ``offset``/``limit`` window. Each candidate
        is first probed with scalar ``length(CAST(field AS BLOB))`` values
        for every returnable text column (``job_id``, ``event``,
        ``metadata``, ``created_at``) so an oversized row is never
        materialized into Python first. A NULL length for one of those
        columns is counted as 0 bytes in the pre-check, matching the legacy
        ``events`` leniency (``metadata`` NULL or empty parses to ``{}`` via
        ``json.loads(value or "{}")``; ``event``/``created_at`` NULL pass
        through unchanged as JSON null) — the pre-check never refuses merely
        because such a length is NULL. Only structural problems refuse the
        call outright: a NULL or non-integer event id in the probe, or a
        fetched row that no longer matches the requested ``job_id`` and
        exact id. If the next row's raw text total already exceeds
        ``max_response_bytes``, return the preceding nonempty page without
        fetching that row; when it is the first row, raise ``ValueError``.
        Continuation thus reaches the oversized row without silently skipping it.

        Only rows passing that probe are then fetched by their exact id, and
        ``metadata`` is decoded leniently exactly as ``events`` does, via
        ``json.loads(value or "{}")``, preserving column and id order.
        Budget accounting matches the actual JSON response: each row's
        ``json.dumps(..., allow_nan=False)`` encoding (with the default
        ``ensure_ascii=True`` escapes) is accumulated row by row, including
        the ``", "`` separators, the dynamic ``next_offset`` digits, and a
        reservation for the canonical response envelope ``{"success": true,
        "data": {"job_id", "events", "next_offset", "next_cursor"}}`` (the
        canonical envelope appends ``next_cursor`` as the decimal form of
        ``next_offset`` and the legacy envelope never carries more). Even an
        empty page must fit its complete reserved envelope. The full page is
        never materialized before budgeting. If the next row's encoding no
        longer fits: with rows already accumulated the page is returned short
        and the next offset
        stays continuous at ``offset + len(page)``; as the very first row it
        raises ``ValueError`` — never an empty success and never a skip.
        Malformed metadata JSON, non-finite numbers (refused by
        ``allow_nan=False``), and SQLite errors propagate as plain
        exceptions for the daemon to map into concise refusals; no row
        content is echoed and nothing is printed.

        The byte bound caps only this method's Python-side allocation and is
        an engineering bound: SQLite's internal C allocations, process RSS,
        and metadata retention policy are outside it and unknown here.
        """
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("job_id must be a non-empty string")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 through 1000")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")

        def _wire_bytes(value: Any) -> int:
            # ensure_ascii defaults to True, so the encoding is pure ASCII
            # wire bytes; allow_nan=False makes non-finite numbers fail.
            return len(json.dumps(value, allow_nan=False).encode("ascii"))

        def _empty_envelope_bytes(next_offset: int) -> int:
            return _wire_bytes({
                "success": True,
                "data": {
                    "job_id": job_id,
                    "events": [],
                    "next_offset": next_offset,
                    "next_cursor": str(next_offset),
                },
            })

        with self.lock:
            if not self.db.in_transaction:
                raise ValueError(
                    "job_log_page_bounded requires an active caller-owned transaction"
                )
            if len(job_id) > max_response_bytes or _empty_envelope_bytes(offset) > max_response_bytes:
                raise ValueError("empty job event page exceeds the response byte budget")
            probes = self.db.execute(
                "SELECT id,length(CAST(job_id AS BLOB)) AS job_id_bytes,"
                "length(CAST(event AS BLOB)) AS event_bytes,"
                "length(CAST(metadata AS BLOB)) AS metadata_bytes,"
                "length(CAST(created_at AS BLOB)) AS created_at_bytes "
                "FROM job_events WHERE job_id=? ORDER BY id LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            ).fetchall()
            page: list[dict[str, Any]] = []
            row_bytes = 0
            for probe in probes:
                if probe["id"] is None or type(probe["id"]) is not int:
                    raise ValueError(
                        "job event page refused: malformed event id"
                    )
                raw_bytes = 0
                for column in ("job_id_bytes", "event_bytes",
                               "metadata_bytes", "created_at_bytes"):
                    # A NULL length means a SQL NULL value; the legacy
                    # events() path still returns such a row, so count 0.
                    if probe[column] is not None:
                        raw_bytes += int(probe[column])
                if raw_bytes > max_response_bytes:
                    if page:
                        break
                    raise ValueError(
                        "job event row exceeds the response byte budget"
                    )
                event_id = int(probe["id"])
                row = self.db.execute(
                    "SELECT id,job_id,event,metadata,created_at FROM job_events "
                    "WHERE job_id=? AND id=?",
                    (job_id, event_id),
                ).fetchone()
                if (row is None or row["job_id"] != job_id
                        or row["id"] is None or int(row["id"]) != event_id):
                    raise ValueError(
                        "job event page refused: row identity does not match "
                        "the requested job and exact id"
                    )
                item = dict(row)
                item["metadata"] = json.loads(item["metadata"] or "{}")
                encoded = _wire_bytes(item)
                count = len(page) + 1
                envelope = _empty_envelope_bytes(offset + count)
                projected = envelope + row_bytes + encoded + (2 * (count - 1))
                if projected > max_response_bytes:
                    if not page:
                        raise ValueError(
                            "first job event row alone does not fit the "
                            "response byte budget"
                        )
                    break
                page.append(item)
                row_bytes += encoded
            return page

    def _job_event_snapshot_page(self, job_id: str, snapshot_last_id: int,
                                 *, limit: int, offset: int) -> list[dict[str, Any]]:
        """Read one page constrained to the caller's immutable event-id bound."""
        rows = self.db.execute(
            "SELECT id,job_id,event,metadata,created_at FROM job_events "
            "WHERE job_id=? AND id<=? ORDER BY id LIMIT ? OFFSET ?",
            (job_id, snapshot_last_id, limit, offset),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(
                    item.get("metadata"),
                    parse_constant=_reject_nonstandard_json_constant,
                )
            except (TypeError, UnicodeDecodeError, ValueError):
                item["metadata"] = None
            result.append(item)
        return result

    def collect_job_event_snapshot(self, job_id: str, *,
                                   page_size: int = JOB_EVENT_SNAPSHOT_PAGE_SIZE) -> dict[str, Any]:
        """Collect one exact job's full event log in a bounded SQLite snapshot.

        The first read fixes the WAL snapshot; every page is additionally
        bounded by the captured maximum event id. A later event append cannot
        shift offsets or be mixed into this receipt. The collector retains all
        rows for downstream validation and returns a compact, recomputable
        page/id manifest.
        """
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("job_id must be a non-empty string")
        if type(page_size) is not int or not 1 <= page_size <= JOB_EVENT_SNAPSHOT_PAGE_SIZE:
            raise ValueError(
                f"page_size must be an integer from 1 through {JOB_EVENT_SNAPSHOT_PAGE_SIZE}"
            )

        issues: list[str] = []
        events: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        event_ids: list[int] = []
        page_count = 0
        expected_event_count = 0
        snapshot_last_id = 0
        job_row_count = 0
        terminal_sentinel: dict[str, Any] = {
            "checked": False, "job_id": job_id, "snapshot_last_id": 0,
            "offset": 0, "count": 0, "event_id": None,
        }

        with self.lock:
            self.db.execute("BEGIN")
            try:
                # Reading the exact job row establishes its binding within the
                # same SQLite read transaction as count/max and all page reads.
                job_rows = self.db.execute(
                    "SELECT job_id FROM jobs WHERE job_id=?", (job_id,),
                ).fetchall()
                job_row_count = len(job_rows)
                if job_row_count != 1:
                    issues.append("exact job row cardinality is not one")
                aggregate = self.db.execute(
                    "SELECT COUNT(*) AS event_count,COALESCE(MAX(id),0) AS last_id "
                    "FROM job_events WHERE job_id=?", (job_id,),
                ).fetchone()
                expected_event_count = int(aggregate["event_count"])
                snapshot_last_id = int(aggregate["last_id"])
                page_count = ((expected_event_count + page_size - 1) // page_size
                              if expected_event_count else 0)
                if page_count > JOB_EVENT_SNAPSHOT_MAX_PAGES:
                    issues.append("job event snapshot exceeds the bounded page limit")
                else:
                    previous_id = 0
                    for page_index in range(page_count):
                        offset = page_index * page_size
                        expected_count = min(page_size, expected_event_count - offset)
                        page_rows = self._job_event_snapshot_page(
                            job_id, snapshot_last_id, limit=page_size, offset=offset,
                        )
                        page_ids: list[int] = []
                        for item in page_rows:
                            row_id = item.get("id")
                            if (item.get("job_id") != job_id
                                    or type(row_id) is not int or row_id <= previous_id
                            or row_id > snapshot_last_id
                            or not isinstance(item.get("event"), str)
                            or not item.get("event")
                            or not isinstance(item.get("metadata"), dict)):
                                issues.append("job event page contains a foreign, malformed, duplicate, or unordered row")
                            if type(row_id) is int:
                                page_ids.append(row_id)
                                previous_id = row_id
                            events.append(item)
                        if len(page_rows) != expected_count:
                            issues.append("job event page cardinality differs from its frozen offset/count")
                        event_ids.extend(page_ids)
                        pages.append({
                            "index": page_index,
                            "offset": offset,
                            "count": len(page_rows),
                            "first_id": page_ids[0] if page_ids else None,
                            "last_id": page_ids[-1] if page_ids else None,
                            "id_sha256": hashlib.sha256(
                                ",".join(str(value) for value in page_ids).encode("ascii")
                            ).hexdigest(),
                            "events_sha256": _job_event_snapshot_sha256(page_rows),
                        })

                    sentinel = self.db.execute(
                        "SELECT id FROM job_events WHERE job_id=? AND id<=? "
                        "ORDER BY id LIMIT 1 OFFSET ?",
                        (job_id, snapshot_last_id, expected_event_count),
                    ).fetchone()
                    terminal_sentinel = {
                        "checked": True, "job_id": job_id,
                        "snapshot_last_id": snapshot_last_id,
                        "offset": expected_event_count,
                        "count": 1 if sentinel is not None else 0,
                        "event_id": int(sentinel[0]) if sentinel is not None else None,
                    }
                    if sentinel is not None:
                        issues.append("bounded terminal sentinel found an uncounted event")

                if len(events) != expected_event_count:
                    issues.append("fetched job event count differs from the finite count snapshot")
                if len(event_ids) != expected_event_count or event_ids != sorted(set(event_ids)):
                    issues.append("job event id list is incomplete, duplicated, or unordered")
                if ((event_ids and event_ids[-1] != snapshot_last_id)
                        or (not event_ids and snapshot_last_id != 0)):
                    issues.append("last event id differs from the captured snapshot maximum")
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

        id_sha256 = hashlib.sha256(
            ",".join(str(value) for value in event_ids).encode("ascii")
        ).hexdigest()
        events_sha256 = _job_event_snapshot_sha256(events)
        complete = not issues and job_row_count == 1 and terminal_sentinel["checked"] is True
        return {
            "schema": JOB_EVENT_SNAPSHOT_SCHEMA,
            "job_id": job_id,
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "complete": complete,
            "page_size": page_size,
            "max_page_count": JOB_EVENT_SNAPSHOT_MAX_PAGES,
            "expected_page_count": page_count,
            "expected_event_count": expected_event_count,
            "fetched_event_count": len(events),
            "snapshot_last_id": snapshot_last_id,
            "event_ids": event_ids,
            "id_sha256": id_sha256,
            "events_sha256": events_sha256,
            "pages": pages,
            "terminal_sentinel": terminal_sentinel,
            "terminal_sentinel_checked": terminal_sentinel["checked"],
            "terminal_sentinel_count": terminal_sentinel["count"],
            "job_row_count": job_row_count,
            "issues": issues,
            "events": events,
        }

    def fold_job_event_prefix(self, job_id: str, *,
                              snapshot_count: int | None, snapshot_last_id: int | None,
                              max_row_bytes: int,
                              visitor: Callable[[dict[str, Any]], None],
                              _join_read_snapshot: bool = False) -> dict[str, Any]:
        """Fold one exact job's frozen event prefix through a visitor in one read snapshot.

        Additive internal streaming slice (T35): not a public MCP operation;
        the daemon's bounded ``solver_log_read`` path now calls it as its fold
        backend, and it changes no old method signature, stage receipt field,
        schema, retention rule, project authority, or public route. Both
        snapshot inputs are required keyword arguments:
        ``(None, None)`` freezes a fresh whole-job prefix, while a validated
        integer pair re-folds exactly that previously issued prefix (a zero
        count must carry a zero last event id). ``max_row_bytes`` is a
        caller-supplied positive bound in UTF-8 bytes for application row
        admission only; it does not bound SQLite's internal C allocation,
        caller-retained page/progress/result data, or any process-wide memory.

        Semantics: one ``BEGIN`` read transaction under the store RLock reads
        the exact job row plus a count/max aggregate first, freezing the SQLite
        snapshot before iteration. Fresh mode captures count/max; continuation
        mode counts and maxes only rows ``id <= snapshot_last_id`` and must
        match the supplied pair before any row is visited, failing incomplete
        on mismatch. Rows then stream through fixed-size keyset batches
        (``id > previous_id AND id <= captured_last_id ORDER BY id``); no
        OFFSET pagination over growing data and no accumulation of rows, and
        the count is never used to pre-allocate. Each row's four text-field
        byte lengths are probed via ``length(CAST(field AS BLOB))`` before its
        full fields are fetched; any SQL NULL length or combined length above
        ``max_row_bytes`` stops the fold incomplete without fetching the full
        row, decoding JSON, or invoking the visitor for that row. Row shape and
        strict ``json.loads(..., parse_constant=_reject_nonstandard_json_constant)``
        metadata decoding match ``_job_event_snapshot_page``; a malformed,
        foreign, unordered, nonpositive-id, empty-event, or non-dict-metadata
        row fails incomplete and no partial digest is published (both digests
        are ``None`` on incomplete results). Valid rows are fed to
        ``CanonicalJobEventDigests`` and then to ``visitor`` in id order,
        preserving the exact v1 whole-list id and canonical JSON list digests
        the collector produces for the same prefix.

        Callers must treat visitor state as provisional: earlier callbacks can
        already have happened before a later row fails, so that state must be
        discarded on any incomplete result or exception. Callbacks must not
        write back into the store or manage this transaction. If ``visitor``
        raises, the still-open read transaction is rolled back, the original
        exception is re-raised, and the connection stays usable. A later
        append can never move an already frozen prefix.

        ``_join_read_snapshot`` is a private opt-in (default ``False``) that
        lets this fold run inside an explicit caller-owned read snapshot such
        as ``solver_log_snapshot``. The default keeps the self-owned
        BEGIN/COMMIT/ROLLBACK exactly as before, including behaviour when an
        unrelated outer transaction already exists. Only a literal bool
        ``True`` joins: an already-active transaction on this connection is
        then required (checked under the same RLock), no BEGIN, COMMIT or
        ROLLBACK is issued here, and any failure propagates so the owning
        context closes the transaction. A non-bool value, or a join without
        an active transaction, is refused before any read. All validation,
        output and visitor constraints above are unchanged in both modes.
        """
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("job_id must be a non-empty string")
        if (snapshot_count is None) != (snapshot_last_id is None):
            raise ValueError(
                "snapshot_count and snapshot_last_id must be supplied together or both be None"
            )
        if snapshot_count is not None:
            if type(snapshot_count) is not int:
                raise ValueError("snapshot_count must be a plain integer")
            if snapshot_last_id is None or type(snapshot_last_id) is not int:
                raise ValueError("snapshot_last_id must be a plain integer")
            if snapshot_count < 0 or snapshot_last_id < 0:
                raise ValueError("snapshot bounds must be non-negative integers")
            if (snapshot_count == 0) != (snapshot_last_id == 0):
                raise ValueError("a zero-count frozen prefix must carry a zero last event id")
        if type(max_row_bytes) is not int or max_row_bytes <= 0:
            raise ValueError("max_row_bytes must be a positive integer")
        if not callable(visitor):
            raise ValueError("visitor must be a callable")
        if type(_join_read_snapshot) is not bool:
            raise ValueError("_join_read_snapshot must be a bool")

        fold_batch_rows = 64
        issues: list[str] = []
        job_row_count = 0
        expected_event_count = 0
        captured_last_id = 0
        fetched_event_count = 0
        digests = CanonicalJobEventDigests()

        with self.lock:
            if _join_read_snapshot:
                # Caller-owned snapshot join: no BEGIN here, and no COMMIT
                # or ROLLBACK either (see docstring).  A join without an
                # active transaction is refused before any read.
                if not self.db.in_transaction:
                    raise ValueError(
                        "_join_read_snapshot=True requires an active caller-owned transaction"
                    )
            else:
                self.db.execute("BEGIN")
            try:
                # The exact job row, the aggregate, and every streamed read run
                # inside this one SQLite snapshot, so a concurrent append can
                # never move the frozen prefix.
                job_rows = self.db.execute(
                    "SELECT job_id FROM jobs WHERE job_id=?", (job_id,),
                ).fetchmany(2)
                job_row_count = len(job_rows)
                if job_row_count != 1:
                    issues.append("exact job row cardinality is not one")
                if snapshot_count is None:
                    aggregate = self.db.execute(
                        "SELECT COUNT(*) AS event_count,COALESCE(MAX(id),0) AS last_id "
                        "FROM job_events WHERE job_id=?", (job_id,),
                    ).fetchone()
                    expected_event_count = int(aggregate["event_count"])
                    captured_last_id = int(aggregate["last_id"])
                else:
                    expected_event_count = int(snapshot_count)
                    captured_last_id = int(snapshot_last_id)
                    aggregate = self.db.execute(
                        "SELECT COUNT(*) AS event_count,COALESCE(MAX(id),0) AS last_id "
                        "FROM job_events WHERE job_id=? AND id<=?",
                        (job_id, captured_last_id),
                    ).fetchone()
                    if (int(aggregate["event_count"]) != expected_event_count
                            or int(aggregate["last_id"]) != captured_last_id):
                        issues.append(
                            "frozen continuation prefix count/last-id does not match "
                            "rows at or below its bound"
                        )

                if not issues:
                    previous_id = 0
                    row_failure: str | None = None
                    while row_failure is None:
                        probes = self.db.execute(
                            "SELECT id,length(CAST(job_id AS BLOB)) AS job_id_bytes,"
                            "length(CAST(event AS BLOB)) AS event_bytes,"
                            "length(CAST(metadata AS BLOB)) AS metadata_bytes,"
                            "length(CAST(created_at AS BLOB)) AS created_at_bytes "
                            "FROM job_events WHERE job_id=? AND id>? AND id<=? "
                            "ORDER BY id LIMIT ?",
                            (job_id, previous_id, captured_last_id, fold_batch_rows),
                        ).fetchmany(fold_batch_rows)
                        if not probes:
                            break
                        for probe in probes:
                            field_lengths = (
                                probe["job_id_bytes"], probe["event_bytes"],
                                probe["metadata_bytes"], probe["created_at_bytes"],
                            )
                            if any(length is None for length in field_lengths):
                                row_failure = (
                                    "row field length is SQL NULL; the row byte bound "
                                    "cannot be certified"
                                )
                                break
                            if sum(field_lengths) > max_row_bytes:
                                row_failure = (
                                    "row byte lengths exceed the caller's max_row_bytes "
                                    "admission limit"
                                )
                                break
                            row = self.db.execute(
                                "SELECT id,job_id,event,metadata,created_at "
                                "FROM job_events WHERE id=?", (probe["id"],),
                            ).fetchone()
                            item = dict(row)
                            if (item.get("job_id") != job_id
                                    or list(item) != ["id", "job_id", "event", "metadata", "created_at"]
                                    or type(item.get("id")) is not int
                                    or item["id"] <= previous_id or item["id"] > captured_last_id
                                    or not isinstance(item.get("event"), str) or not item.get("event")):
                                row_failure = (
                                    "job event stream contains a foreign, malformed, "
                                    "duplicate, or unordered row"
                                )
                                break
                            try:
                                item["metadata"] = json.loads(
                                    item["metadata"],
                                    parse_constant=_reject_nonstandard_json_constant,
                                )
                            except (TypeError, UnicodeDecodeError, ValueError):
                                item["metadata"] = None
                            if not isinstance(item["metadata"], dict):
                                row_failure = "job event metadata is malformed or not a JSON object"
                                break
                            digests.add(item)
                            previous_id = item["id"]
                            visitor(item)
                            fetched_event_count += 1
                    if row_failure is not None:
                        issues.append(row_failure)
                    elif fetched_event_count != expected_event_count:
                        issues.append(
                            "folded job event count differs from the frozen count snapshot"
                        )
                    elif ((fetched_event_count and previous_id != captured_last_id)
                          or (not fetched_event_count and captured_last_id != 0)):
                        issues.append(
                            "last folded event id differs from the captured snapshot maximum"
                        )
                if not _join_read_snapshot:
                    self.db.execute("COMMIT")
            except BaseException:
                if not _join_read_snapshot and self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

        complete = not issues
        if complete:
            id_sha256, events_sha256 = digests.snapshot()
        else:
            # Partial evidence must never look like an authenticated prefix.
            id_sha256 = events_sha256 = None
        return {
            "job_id": job_id,
            "complete": complete,
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "expected_event_count": expected_event_count,
            "fetched_event_count": fetched_event_count,
            "snapshot_last_id": captured_last_id,
            "id_sha256": id_sha256,
            "events_sha256": events_sha256,
            "issues": issues,
        }

    @contextmanager
    def solver_log_snapshot(self, job_id: str) -> Iterator[dict[str, Any] | None]:
        """One owned SQLite read snapshot plus a result-free job view (T35).

        Additive internal primitive for the ``solver_log_read`` integration
        path: not a public MCP operation, and it authenticates or authorizes
        nothing — a consistent metadata view is not proof of a principal.

        Under the store RLock this context refuses a pre-existing connection
        transaction instead of adopting somebody else's writes, then starts
        its own ``BEGIN`` and reads the exact ``jobs`` row (that table has no
        result column).  When the job exists, its linked operation row is
        read with every existing column except ``result`` explicitly
        enumerated.  Metadata and effective_timeouts decode exactly as
        ``_job``/``_op`` do — malformed JSON fails closed before the yield —
        and a missing operation yields ``job['operation'] = None``.  The
        yielded value mirrors the ``_job`` shape, but the stored result is
        never fetched: ``job['result']`` and the operation view's ``result``
        are both ``None``.  A missing job yields ``None``.

        The lock and the open snapshot are held across the yield so a
        same-thread caller can run
        ``fold_job_event_prefix(..., _join_read_snapshot=True)`` and
        ``project_operation_result_error`` against this one consistent view.
        Normal exit COMMITs the owned transaction; any exception (caller
        raise, GeneratorExit, failed COMMIT) ROLLBACKs — only ever this
        context's own transaction, because a foreign active transaction was
        refused above — and re-raises unchanged.
        """
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("job_id must be a non-empty string")
        with self.lock:
            if self.db.in_transaction:
                raise ValueError(
                    "solver_log_snapshot cannot begin inside a pre-existing transaction"
                )
            self.db.execute("BEGIN")
            try:
                job_rows = self.db.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,),
                ).fetchmany(2)
                if not job_rows:
                    # The owned snapshot stays open across the yield even
                    # for a missing job so the caller sees one consistent
                    # view of the whole read.
                    snapshot: dict[str, Any] | None = None
                elif len(job_rows) != 1:
                    raise RuntimeError("exact job row cardinality is not one")
                else:
                    job_row = job_rows[0]
                    job = dict(job_row)
                    job["metadata"] = json.loads(job["metadata"] or "{}")
                    job["effective_timeouts"] = json.loads(
                        job.get("effective_timeouts") or "{}"
                    )
                    op_rows = self.db.execute(
                        "SELECT operation_id,request_id,idempotency_key,request_hash,"
                        "operation,status,metadata,created_at,started_at,finished_at,"
                        "effective_timeouts FROM operations WHERE operation_id=?",
                        (job_row["operation_id"],),
                    ).fetchmany(2)
                    if not op_rows:
                        operation: dict[str, Any] | None = None
                    elif len(op_rows) != 1:
                        raise RuntimeError("exact operation row cardinality is not one")
                    else:
                        operation = dict(op_rows[0])
                        operation["metadata"] = json.loads(operation["metadata"] or "{}")
                        operation["effective_timeouts"] = json.loads(
                            operation.get("effective_timeouts") or "{}"
                        )
                        # The stored result is deliberately not fetched;
                        # project_operation_result_error reads it in bounded
                        # slices inside this same snapshot when required.
                        operation["result"] = None
                    job["operation"] = operation
                    job["result"] = None
                    snapshot = job
                yield snapshot
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def project_operation_result_error(self, operation_id: str, *,
                                       max_error_bytes: int) -> dict[str, Any] | None:
        """Budgeted streaming projection of one stored result's ``error`` field.

        T35 companion to ``solver_log_snapshot``: it runs only while an
        active transaction exists on this connection and on the same thread
        holding the RLock — in practice inside that owned read snapshot —
        and it never starts, commits or rolls back SQL itself.  The stored
        result is never fetched whole into Python: SQL returns only scalars
        (``typeof``/``length``, plus an ``instr(result,char(0))`` NUL probe
        for TEXT) and bounded ``substr`` slices, which are fed once, in
        order, as UTF-8 byte chunks to ``project_error_chunks``.  The
        implementation deliberately avoids ``sqlite3.blobopen`` (a newer
        API) so it works on the project's ``requires-python >= 3.10``
        baseline, and never uses ``json_extract`` — SQLite duplicate-key
        handling is not equivalent to ``json.loads``.

        Contract:
        * A missing exact ``operation_id`` raises ``KeyError``.  ``NULL`` or
          an empty TEXT/BLOB result maps to ``None`` exactly like the
          falsy-result branch of ``_op``; an integer/real stored value is an
          unsupported type and fails closed.
        * SQLite ``length(TEXT)`` and ``substr`` stop at a raw NUL, so TEXT
          is first probed with ``instr(result,char(0))`` and any raw NUL
          raises ``ValueError`` instead of silently truncating.
        * TEXT is sliced from position 1 in SQLite character units (16384
          characters per slice, returned as ``str`` and re-encoded with
          strict UTF-8, so unencodable text raises); BLOB is sliced in byte
          units (65536 bytes per slice).  The scan advances by the actually
          returned unit count; an empty or wrong-typed slice, a malformed
          probe or any incomplete scan raises rather than pretending there
          is no error.
        * The return is exactly the projection's: ``{'error': value}`` under
          last-wins duplicate-key semantics, or ``{}`` only for a fully
          valid document with no top-level ``error``.  Document damage,
          refused encoding/depth/integer boundaries and an over-budget
          selected error all propagate; nothing is truncated or disguised
          as "no error".

        The bounded slices bound only this method's Python-side residency
        for the tested boundary.  Repeated SQLite rescans from the value
        start, SQLite C allocations, time, RSS and metadata size are not
        guaranteed, and ``length()``/``substr()`` character-unit
        equivalence with Python ``str`` lengths is scoped to what probes
        have observed on the selected CPython/SQLite build; a mismatch is
        expected to end in an empty/short-slice refusal or a stream error,
        but exotic unit-counting builds remain unprobed.
        """
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("operation_id must be a non-empty string")
        if (isinstance(max_error_bytes, bool) or not isinstance(max_error_bytes, int)
                or max_error_bytes < 0):
            raise ValueError("max_error_bytes must be a non-negative int")
        with self.lock:
            if not self.db.in_transaction:
                raise ValueError(
                    "project_operation_result_error requires the active read transaction "
                    "owned by solver_log_snapshot"
                )
            probe_rows = self.db.execute(
                "SELECT typeof(result) AS result_type,length(result) AS result_length "
                "FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchmany(2)
            if not probe_rows:
                raise KeyError(f"operation {operation_id!r} does not exist")
            if len(probe_rows) != 1:
                raise RuntimeError("exact operation row cardinality is not one")
            result_type = probe_rows[0]["result_type"]
            stored_length = probe_rows[0]["result_length"]
            if result_type == "null":
                return None
            if result_type not in ("text", "blob"):
                raise ValueError(f"unsupported stored result type {result_type!r}")
            if type(stored_length) is not int or stored_length < 0:
                raise RuntimeError("stored result length probe is malformed")
            if result_type == "text":
                nul_probe = self.db.execute(
                    "SELECT instr(result,char(0)) AS nul_position "
                    "FROM operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if nul_probe is None or type(nul_probe["nul_position"]) is not int:
                    raise RuntimeError("stored result NUL probe is malformed")
                if nul_probe["nul_position"] != 0:
                    raise ValueError(
                        "stored TEXT result contains a raw NUL; SQLite length/substr "
                        "stop at it and a raw NUL is not valid JSON"
                    )
            if stored_length == 0:
                # Check TEXT for a leading NUL before treating its SQLite
                # length of zero as a genuinely empty stored result.
                return None
            text_chunk_characters = 16384
            blob_chunk_bytes = 65536

            def text_chunks() -> Iterator[bytes]:
                position = 1
                remaining = stored_length
                while remaining > 0:
                    row = self.db.execute(
                        "SELECT substr(result,?,?) AS chunk FROM operations WHERE operation_id=?",
                        (position, text_chunk_characters, operation_id),
                    ).fetchone()
                    chunk = row["chunk"] if row is not None else None
                    if not isinstance(chunk, str) or not chunk:
                        raise RuntimeError(
                            f"stored TEXT scan returned an empty or non-text slice at "
                            f"character {position}; the scan is incomplete"
                        )
                    # Strict UTF-8 re-encoding; unencodable text raises here.
                    yield chunk.encode("utf-8")
                    consumed = len(chunk)
                    position += consumed
                    remaining -= consumed

            def blob_chunks() -> Iterator[bytes]:
                position = 1
                remaining = stored_length
                while remaining > 0:
                    row = self.db.execute(
                        "SELECT substr(result,?,?) AS chunk FROM operations WHERE operation_id=?",
                        (position, blob_chunk_bytes, operation_id),
                    ).fetchone()
                    chunk = row["chunk"] if row is not None else None
                    if not isinstance(chunk, (bytes, bytearray)) or not chunk:
                        raise RuntimeError(
                            f"stored BLOB scan returned an empty or non-byte slice at "
                            f"byte {position}; the scan is incomplete"
                        )
                    yield bytes(chunk)
                    consumed = len(chunk)
                    position += consumed
                    remaining -= consumed

            chunks = text_chunks() if result_type == "text" else blob_chunks()
            return project_error_chunks(chunks, max_error_bytes=max_error_bytes)

    def compact_terminal_job_metadata(self, job_ids: list[str], *, project_id: str | None = None) -> list[str]:
        """Compact selected terminal job-view metadata without deleting audit data.

        Operations, idempotency hashes/keys, results, job events, artifact rows,
        and checkpoint rows remain untouched. The compacted job row keeps a
        top-level project_id derived solely from the consistent job/operation
        metadata binding (never promoted from arguments), project_root only as
        legacy descriptive metadata, and a cleanup tombstone; a single durable
        event records the action.
        """
        if not job_ids or any(not isinstance(job_id, str) or not job_id for job_id in job_ids):
            raise JobCleanupError("INVALID_REQUEST", "job_ids must contain non-empty strings")
        if len(set(job_ids)) != len(job_ids):
            raise JobCleanupError("INVALID_REQUEST", "job_ids must not contain duplicates")
        if project_id is not None and (not isinstance(project_id, str) or not project_id):
            raise JobCleanupError("INVALID_REQUEST", "project_id must be a non-empty string when provided")

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
                    decode_failed = False
                    try:
                        job_metadata = json.loads(row["job_metadata"] or "{}")
                        operation_metadata = json.loads(row["operation_metadata"] or "{}")
                    except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
                        # A selected job/operation metadata decode failure can
                        # establish no project binding; classify it as no
                        # binding so the scoped/unscoped branch below refuses
                        # the whole batch before any update or event. The data
                        # is neither repaired nor silently skipped, and the
                        # placeholders never reach the selected entries.
                        decode_failed = True
                        job_metadata = {}
                        operation_metadata = {}
                    binding = None if decode_failed else consistent_project_id(job_metadata, operation_metadata)
                    if project_id is not None:
                        if binding != project_id:
                            raise JobCleanupError("PROJECT_SCOPE_MISMATCH", f"job {job_id} is not recorded in project {project_id}")
                    elif binding is None:
                        raise JobCleanupError("PROJECT_BINDING_UNKNOWN", f"job {job_id} has no unambiguous project binding")
                    if dependency_values([job_metadata, operation_metadata]):
                        raise JobCleanupError("JOB_HAS_DEPENDENCIES", f"job {job_id} carries linked job dependencies")
                    selected.append({
                        "job_id": job_id,
                        "operation_id": row["operation_id"],
                        "status": row["status"],
                        "job_metadata": job_metadata,
                        "binding": binding,
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
                    # The compacted top-level project_id comes only from the
                    # helper-derived job/operation binding established above;
                    # arguments.project_id can neither create nor override it.
                    compacted: dict[str, Any] = {"project_id": row["binding"]}
                    for source in (old_metadata, old_metadata.get("arguments", {}), old_metadata.get("execution", {})):
                        if isinstance(source, dict) and "project_root" in source and source["project_root"] is not None:
                            compacted.setdefault("project_root", source["project_root"])
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

    def reconcile_after_restart(
        self, *,
        preserve_verified_session_start: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[str]:
        """Move interrupted jobs to RECONCILING without reopening verified starts.

        A caller may preserve an UNKNOWN ``session.start`` only by validating
        its complete durable lifecycle-recovery proof. No other operation or
        status is eligible for this exception.
        """
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM jobs WHERE status NOT IN "
                "('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST','RECONCILING')"
            ).fetchall()
            job_ids = []
            for row in rows:
                if (row["status"] == "UNKNOWN"
                        and preserve_verified_session_start is not None):
                    job = self._job(row)
                    operation = job.get("operation")
                    if (isinstance(operation, dict)
                            and operation.get("operation") == "session.start"
                            and preserve_verified_session_start(job) is True):
                        continue
                job_ids.append(row["job_id"])
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

    def persist_runtime_snapshot(self, state: dict[str, Any], project_bindings: dict[str, str | None],
                                 legacy_lineage: dict[str, dict[str, Any]], project_authorizer: Any) -> None:
        """Publish a ledger snapshot and its revision records in one transaction."""
        from ._execution_contract import ExecutionContractError

        session_id = state["session_id"]
        identity = state["worker_identity"]
        runtime_id = identity.get("runtime_id") or identity.get("worker_instance_id")
        if not isinstance(runtime_id, str) or not runtime_id:
            raise ValueError("worker identity requires runtime_id or worker_instance_id")
        with self.lock:
            if self.db.in_transaction:
                raise RuntimeError("cannot persist runtime snapshot inside an existing transaction")
            try:
                self.db.execute("BEGIN IMMEDIATE")
                legacy_projects = {}
                if legacy_lineage:
                    row = self.db.execute("SELECT metadata FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                    saved = json.loads(row[0]) if row is not None else None
                    if not isinstance(saved, dict):
                        raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "legacy session lineage is unavailable")
                    saved_identity = saved.get("worker_identity")
                    if (not isinstance(saved_identity, dict)
                            or saved_identity.get("worker_instance_id") != identity.get("worker_instance_id")
                            or type(saved_identity.get("connection_epoch")) is not int
                            or saved_identity.get("connection_epoch") != identity.get("connection_epoch")
                            or saved.get("server_instance_id") != state.get("server_instance_id")):
                        raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "legacy Worker lineage changed")
                    for tag, old_ref in legacy_lineage.items():
                        saved_model = saved.get("models", {}).get(tag, {})
                        saved_ref = saved_model.get("ref") if isinstance(saved_model, dict) else None
                        current_model = state["models"].get(tag)
                        if (not isinstance(saved_ref, dict) or old_ref.get("schema_version") is not True
                                or json.dumps(saved_ref, sort_keys=True) != json.dumps(old_ref, sort_keys=True)
                                or not isinstance(current_model, dict)
                                or json.dumps({**old_ref, "schema_version": 1}, sort_keys=True)
                                != json.dumps(current_model["ref"], sort_keys=True)):
                            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "legacy ModelRef lineage changed")
                        old_key = json.dumps(old_ref, sort_keys=True, separators=(",", ":"))
                        new_key = json.dumps(current_model["ref"], sort_keys=True, separators=(",", ":"))
                        projects = []
                        for model_key in (old_key, new_key):
                            item = self.db.execute("SELECT metadata FROM revisions WHERE model_key=?", (model_key,)).fetchone()
                            metadata = json.loads(item[0]) if item is not None else {}
                            if not isinstance(metadata, dict):
                                raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "legacy project binding is malformed")
                            project = metadata.get("project_id")
                            if project is not None:
                                if not isinstance(project, str) or not project:
                                    raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "legacy project binding is invalid")
                                projects.append(project)
                        claimed = project_bindings.get(new_key)
                        if claimed is not None and (not projects or claimed not in projects):
                            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "legacy project claim is not durably bound")
                        if len(set(projects)) > 1:
                            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "legacy and current project bindings conflict")
                        project = projects[0] if projects else None
                        if project is not None:
                            if not callable(project_authorizer):
                                raise ExecutionContractError("PERMISSION_DENIED", "legacy project authority is unavailable")
                            project_authorizer(project, "project_write")
                        legacy_projects[new_key] = project
                for table, column, key in (("sessions", "session_id", session_id),
                                           ("runtimes", "runtime_id", runtime_id)):
                    row = self.db.execute(f"SELECT metadata FROM {table} WHERE {column}=?", (key,)).fetchone()
                    current = json.loads(row[0]) if row is not None else {}
                    if not isinstance(current, dict):
                        raise RuntimeError(f"persisted {table} metadata is malformed")
                    merged = {**current, **state}
                    self.db.execute(
                        f"INSERT INTO {table}({column},metadata) VALUES(?,?) "
                        f"ON CONFLICT({column}) DO UPDATE SET metadata=excluded.metadata",
                        (key, json.dumps(merged, sort_keys=True)),
                    )
                for model in state["models"].values():
                    ref = model["ref"]
                    key = json.dumps(ref, sort_keys=True, separators=(",", ":"))
                    row = self.db.execute("SELECT metadata FROM revisions WHERE model_key=?", (key,)).fetchone()
                    previous = json.loads(row[0]) if row is not None else {}
                    stored_project = previous.get("project_id")
                    claimed_project = project_bindings.get(key)
                    if (claimed_project is not None and stored_project is not None
                            and claimed_project != stored_project):
                        raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "ModelRef project binding changed")
                    project_id = (legacy_projects[key] if key in legacy_projects else
                                  claimed_project if claimed_project is not None else stored_project)
                    metadata = {
                        "model_ref": ref, "revision": model["revision"], "dirty": model["dirty"],
                        "fingerprint": model["fingerprint"], "active_operation_id": model["active_operation_id"],
                        "attribution": "PROJECT_BOUND" if isinstance(project_id, str) else "UNATTRIBUTED",
                    }
                    if isinstance(project_id, str):
                        metadata["project_id"] = project_id
                    self.db.execute(
                        "INSERT INTO revisions(model_key,metadata,revision) VALUES(?,?,?) "
                        "ON CONFLICT(model_key) DO UPDATE SET metadata=excluded.metadata,revision=excluded.revision",
                        (key, json.dumps(metadata, sort_keys=True), int(metadata["revision"])),
                    )
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

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

    def _update_session_lifecycle_in_transaction(self, session_id, updater):
        """Same lifecycle updater under an existing Store-owned write transaction."""
        if not self.db.in_transaction:
            raise RuntimeError("lifecycle update requires the existing Store write transaction")
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
        return replacement

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
                replacement = self._update_session_lifecycle_in_transaction(session_id, updater)
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

    def list_project_artifact_page(
        self,
        *,
        project_id: str,
        project_root_identity: str,
        host_identity: str,
        engine_host_identity: str,
        filters: dict[str, str],
        after_artifact_id: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Read one bounded, project-scoped page of v2 artifact metadata.

        The index and WHERE clause exclude malformed JSON and every record not
        bound to the exact project/root/host/engine tuple. This deliberately
        does not fall back to ``list_metadata`` or scan the filesystem.
        """
        identities = (project_id, project_root_identity, host_identity, engine_host_identity)
        if any(not isinstance(value, str) or not value for value in identities):
            raise ValueError("artifact page scope identities must be nonempty strings")
        if not isinstance(filters, dict) or set(filters) - {"role", "classification"}:
            raise ValueError("unsupported artifact page filter")
        if any(not isinstance(value, str) or not value for value in filters.values()):
            raise ValueError("artifact page filters must be nonempty strings")
        if after_artifact_id is not None and (
                not isinstance(after_artifact_id, str) or len(after_artifact_id) != 64):
            raise ValueError("artifact page cursor key is malformed")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("artifact page limit must be between 1 and 200")

        clauses = [f"({_artifact_json_value(field)}) = ?" for field in _ARTIFACT_SCOPE_JSON_FIELDS]
        params: list[Any] = [2, *identities]
        for field in ("role", "classification"):
            if field in filters:
                clauses.append(f"({_artifact_json_value(field)}) = ?")
                params.append(filters[field])
        if after_artifact_id is not None:
            clauses.append("artifact_id > ?")
            params.append(after_artifact_id)
        params.append(limit + 1)
        query = (
            f"SELECT artifact_id, metadata FROM artifacts INDEXED BY {ARTIFACT_SCOPE_INDEX} "
            f"WHERE {' AND '.join(clauses)} ORDER BY artifact_id ASC LIMIT ?"
        )
        with self.lock:
            rows = self.db.execute(query, params).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        output: list[dict[str, Any]] = []
        for row in page_rows:
            try:
                record = json.loads(row["metadata"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("scoped artifact metadata is malformed") from exc
            key = row["artifact_id"]
            if (not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None
                    or not isinstance(record, dict) or type(record.get("schema_version")) is not int
                    or record.get("schema_version") != 2
                    or record.get("artifact_id") != key or record.get("sha256") != key
                    or record.get("project_id") != project_id
                    or record.get("project_root_identity") != project_root_identity
                    or record.get("host_identity") != host_identity
                    or record.get("engine_host_identity") != engine_host_identity):
                raise ValueError("scoped artifact metadata identity is malformed")
            output.append(record)
        return output, has_more

    def get_project_artifact_metadata(
        self,
        artifact_id: str,
        *,
        project_id: str,
        project_root_identity: str,
        host_identity: str,
        engine_host_identity: str,
    ) -> dict[str, Any] | None:
        """Fetch a registration only when its complete v2 scope matches."""
        identities = (project_id, project_root_identity, host_identity, engine_host_identity)
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("artifact_id must be nonempty")
        if any(not isinstance(value, str) or not value for value in identities):
            raise ValueError("artifact lookup scope identities must be nonempty strings")
        clauses = [
            f"({_artifact_json_value(field)}) = ?"
            for field in _ARTIFACT_SCOPE_JSON_FIELDS
        ]
        params: list[Any] = [2, *identities, artifact_id]
        query = (
            f"SELECT artifact_id, metadata FROM artifacts INDEXED BY {ARTIFACT_SCOPE_INDEX} "
            f"WHERE {' AND '.join(clauses)} AND artifact_id = ? LIMIT 1"
        )
        with self.lock:
            row = self.db.execute(query, params).fetchone()
        if row is None:
            return None
        try:
            record = json.loads(row["metadata"])
        except (TypeError, json.JSONDecodeError) as exc:
            # The scope expression already excludes malformed JSON. Keep this
            # guard in case SQLite's JSON decoder and Python disagree.
            raise ValueError("scoped artifact metadata is malformed") from exc
        if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
                or record.get("schema_version") != 2
                or row["artifact_id"] != artifact_id
                or record.get("artifact_id") != artifact_id or record.get("sha256") != artifact_id
                or record.get("project_id") != project_id
                or record.get("project_root_identity") != project_root_identity
                or record.get("host_identity") != host_identity
                or record.get("engine_host_identity") != engine_host_identity):
            raise ValueError("scoped artifact metadata identity is malformed")
        return record

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
                if (status == "ACCEPTED" or acceptance_status in {"ACCEPTED", "INITIAL_OUTPUT_ACCEPTED"}):
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

    def finalize_initial_stage_output_acceptance(
        self, project_id: str, model_ref: dict[str, Any], attempt_id: str, *,
        expected_version: int, native_evidence: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit only the bounded initial-output acceptance through its dedicated path."""
        from ._stage_contract import canonical_json, sha256_json
        from ._w21_stage_backend import (
            is_registered_initial_state_stage, stage_attempt_binding,
            validate_initial_output_acceptance,
        )

        if (type(expected_version) is not int or expected_version < 1
                or not isinstance(native_evidence, dict) or not isinstance(result, dict)):
            raise StagePlanStoreConflict("INVALID_REQUEST", "initial-output finalizer fields are malformed")
        digest = self._stage_scope_digest(project_id, model_ref)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT * FROM stage_attempts WHERE scope_digest=? AND attempt_id=?",
                    (digest, attempt_id),
                ).fetchone()
                if row is None:
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_NOT_FOUND", "stage attempt is not registered in this scope")
                current = self._stage_attempt_from_row(row, project_id=project_id, model_ref=model_ref)
                if current.get("version") != expected_version:
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_CAS_CONFLICT", "initial-output attempt changed before acceptance")
                if (current.get("status") != "RUNNING" or current.get("engine_dispatched") is not True
                        or current.get("execution_status") != "RUNNING"):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "initial-output acceptance requires the exact dispatched running attempt")
                resolved = self._load_validated_stage_scope_locked(project_id, model_ref)
                plan = resolved[0].get(current.get("plan_id"))
                index = resolved[1].get(current.get("stage_id"))
                stage = None
                if plan is not None and index is not None:
                    stage_rows = [item for item in plan.get("definition", {}).get("stages", [])
                                  if isinstance(item, dict) and item.get("stage_id") == current.get("stage_id")]
                    stage = stage_rows[0] if len(stage_rows) == 1 else None
                if (plan is None or index is None or stage is None
                        or not is_registered_initial_state_stage(plan, stage)):
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "stored plan is not the registered first initial-state stage")

                binding = stage_attempt_binding(current)
                saved = native_evidence.get("saved_artifact")
                observed_hash = native_evidence.get("saved_artifact_observed_sha256")
                try:
                    artifact_path = Path(saved.get("path")) if isinstance(saved, dict) else None
                    hasher = hashlib.sha256()
                    size = 0
                    if artifact_path is not None:
                        with artifact_path.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                hasher.update(chunk)
                                size += len(chunk)
                    disk_hash = hasher.hexdigest()
                except (OSError, TypeError, ValueError) as exc:
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "saved initial artifact bytes are unavailable") from exc
                if (artifact_path is None or not artifact_path.is_file()
                        or not isinstance(saved, dict) or saved.get("sha256") != disk_hash
                        or observed_hash != disk_hash or saved.get("size") != size):
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "saved initial artifact bytes/hash/size differ")

                preflight = native_evidence.get("initial_stage_preflight")
                preflight_id = preflight.get("artifact_id") if isinstance(preflight, dict) else None
                preflight_record = preflight.get("record") if isinstance(preflight, dict) else None
                if (not isinstance(preflight_id, str) or not isinstance(preflight_record, dict)
                        or self.get_metadata("artifacts", preflight_id) != preflight_record):
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "initial Study preflight is not the exact persisted artifact")

                output = native_evidence.get("native_output_readback")
                output_refs = output.get("evidence_refs") if isinstance(output, dict) else None
                if not isinstance(output_refs, list) or not output_refs:
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "mapped initial field artifacts are absent")
                for ref in output_refs:
                    if not isinstance(ref, dict) or not isinstance(ref.get("artifact_id"), str):
                        raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "mapped field artifact reference is malformed")
                    artifact_record = self.get_metadata("artifacts", ref["artifact_id"])
                    if not isinstance(artifact_record, dict) or artifact_record.get("sha256") != ref.get("sha256"):
                        raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "mapped field artifact is not the exact persisted record")
                    artifact_file = ref.get("file_path")
                    if not isinstance(artifact_file, str):
                        raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "mapped field artifact has no saved file")
                    field_hasher = hashlib.sha256()
                    field_size = 0
                    try:
                        with Path(artifact_file).open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                field_hasher.update(chunk)
                                field_size += len(chunk)
                    except OSError as exc:
                        raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "mapped field artifact bytes are unavailable") from exc
                    if field_hasher.hexdigest() != ref.get("sha256") or field_size != ref.get("byte_size"):
                        raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "mapped field artifact bytes/hash/size differ")

                mesh = native_evidence.get("mesh_snapshot_readback")
                mesh_ref = mesh.get("artifact_ref") if isinstance(mesh, dict) else None
                mesh_record = self.get_metadata("artifacts", mesh_ref.get("artifact_id")) if isinstance(mesh_ref, dict) else None
                mesh_payload = dict(mesh_record) if isinstance(mesh_record, dict) else {}
                mesh_sha = mesh_payload.pop("sha256", None)
                if (not isinstance(mesh_ref, dict) or not isinstance(mesh_record, dict)
                        or native_evidence.get("mesh_snapshot_artifact_record") != mesh_record
                        or mesh_record.get("sha256") != mesh_ref.get("sha256")
                        or mesh_sha != sha256_json(mesh_payload)
                        or mesh_sha != mesh_ref.get("sha256")):
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "current mesh snapshot is not durably recorded")

                native_output = native_evidence.get("native_output_readback")
                current_output_rows = [item for item in current.get("evidence", [])
                                       if isinstance(item, dict) and item.get("kind") == "stage-output-readback"]
                current_artifact_rows = [item for item in current.get("evidence", [])
                                          if isinstance(item, dict) and item.get("kind") == "saved-stage-artifact"]
                if (len(current_output_rows) != 1 or current_output_rows[0].get("status") != "VERIFIED"
                        or current_output_rows[0].get("revision_chain") != native_output.get("revision_chain")
                        or current_output_rows[0].get("output_tuple") != native_output.get("output_tuple")
                        or len(current_artifact_rows) != 1
                        or current_artifact_rows[0].get("path") != saved.get("path")
                        or current_artifact_rows[0].get("sha256") != disk_hash
                        or current_artifact_rows[0].get("size") != size):
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "durable stage-output/artifact rows differ from the accepted evidence")

                actual_dispatches = [item for item in current.get("evidence", [])
                                     if isinstance(item, dict) and item.get("kind") == "worker-dispatch"]
                if native_evidence.get("solve_save_dispatches") != actual_dispatches:
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "solve/save dispatch rows differ from the attempt ledger")
                job = self.operation_job(current.get("operation_id"))
                job_id = job.get("job_id") if isinstance(job, dict) else None
                durable_events = self.events(job_id, limit=1000) if isinstance(job_id, str) else []
                actual_worker_rows = [item for item in durable_events if item.get("event") == "worker_request"]
                if native_evidence.get("worker_event_rows") != actual_worker_rows:
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "Worker evidence differs from the durable job event ledger")

                output = native_evidence.get("native_output_readback")
                solve_revision = output.get("solve_revision") if isinstance(output, dict) else None
                output_revision = output.get("output_revision") if isinstance(output, dict) else None
                valid, missing = validate_initial_output_acceptance(
                    native_evidence, binding=binding, stage=stage,
                    stage_run_operation_id=current["operation_id"],
                    solve_revision=solve_revision, output_revision=output_revision,
                    observed_artifact_sha256=disk_hash,
                )
                if not valid:
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "initial output evidence is incomplete: " + "; ".join(missing[:8]))

                evidence_rows = [*current["evidence"], {
                    "kind": "initial-output-native-evidence",
                    "status": "INITIAL_OUTPUT_ACCEPTED",
                    "native_evidence": native_evidence,
                }]
                updated = dict(current)
                updated.update({
                    "status": "ACCEPTED", "version": expected_version + 1,
                    "engine_dispatched": True, "execution_status": "SOLVE_SUCCEEDED",
                    "acceptance_status": "INITIAL_OUTPUT_ACCEPTED",
                    "evidence": evidence_rows, "result": result,
                })
                updated.pop("sha256", None)
                updated["sha256"] = sha256_json(updated)
                self._validate_stage_attempt_record(updated, project_id=project_id, model_ref=model_ref)
                cursor = self.db.execute(
                    "UPDATE stage_attempts SET status=?,version=?,record_json=?,updated_at=? WHERE attempt_id=? AND version=?",
                    ("ACCEPTED", expected_version + 1, canonical_json(updated),
                     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), attempt_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_CAS_CONFLICT", "initial-output attempt changed during acceptance")
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
