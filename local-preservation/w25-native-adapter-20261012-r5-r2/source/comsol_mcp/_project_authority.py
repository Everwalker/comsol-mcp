"""Project records and host-authorized project policy over the daemon store."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping
from uuid import uuid4

from comsol_mcp._execution_contract import ExecutionContractError


PROJECT_SCHEMA_VERSION = 1
PROJECT_OPERATIONS = frozenset({
    "project.create", "project.inspect", "project.contract_set",
    "project.policy_set", "project.permissions", "project.state_export",
})
PROJECT_PERMISSIONS = frozenset({"inspect", "project_write", "compute", "trusted_code", "host_control"})
SENSITIVE_KEY_NAMES = frozenset({
    "authorization", "authorizationref", "token", "accesstoken", "refreshtoken",
    "password", "passwd", "secret", "clientsecret", "credential", "credentials",
    "apikey", "privatekey",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: Any, *, field: str, maximum_bytes: int = 262_144) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExecutionContractError("INVALID_REQUEST", f"{field} must contain finite JSON values") from exc
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise ExecutionContractError("INVALID_REQUEST", f"{field} exceeds the supported size")
    return encoded


def _sensitive_key(key: str) -> bool:
    folded = re.sub(r"[^a-z0-9]", "", key.casefold())
    return folded in SENSITIVE_KEY_NAMES or folded.endswith(("token", "password", "secret", "credential", "privatekey"))


def _reject_sensitive_fields(value: Any, *, field: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ExecutionContractError("INVALID_REQUEST", f"{field} object keys must be strings")
            if _sensitive_key(key):
                raise ExecutionContractError("INVALID_REQUEST", f"{field} cannot contain credential or authorization fields")
            _reject_sensitive_fields(child, field=field)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive_fields(child, field=field)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class ProjectAuthority:
    """Project CRUD over the daemon's existing SQLite store.

    ``permission_provider`` and ``authorization_verifier`` are trusted host
    injections.  Request data never grants its own permissions.  Policy
    ceilings must also come from trusted deployment configuration.
    """

    def __init__(
        self,
        store: Any,
        *,
        workspace_root: str | Path,
        permission_provider: Callable[[], set[str] | frozenset[str]],
        grant_ceiling: set[str] | frozenset[str],
        authorization_verifier: Callable[[str, str], bool] | None = None,
    ) -> None:
        if not hasattr(store, "db") or not hasattr(store, "lock"):
            raise TypeError("ProjectAuthority requires the existing OperationStore connection and lock")
        if not callable(permission_provider):
            raise TypeError("permission_provider must be supplied by the host")
        self.store = store
        try:
            self.workspace_root = Path(workspace_root).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "authorized workspace root is unavailable") from exc
        if not self.workspace_root.is_dir():
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "authorized workspace root must be a directory")
        self.permission_provider = permission_provider
        if not isinstance(grant_ceiling, (set, frozenset)) or any(
                not isinstance(item, str) or item not in PROJECT_PERMISSIONS for item in grant_ceiling):
            raise ValueError("grant_ceiling must be the trusted daemon-start permission snapshot")
        self.grant_ceiling = frozenset(grant_ceiling)
        self.authorization_verifier = authorization_verifier

    def _permissions(self) -> set[str]:
        try:
            value = self.permission_provider()
        except Exception as exc:
            raise ExecutionContractError("PERMISSION_STATE_UNKNOWN", "host permission state is unavailable") from exc
        if not isinstance(value, (set, frozenset)) or any(
                not isinstance(item, str) or item not in PROJECT_PERMISSIONS for item in value):
            raise ExecutionContractError("PERMISSION_STATE_UNKNOWN", "host permission state is malformed")
        # Startup configuration is a ceiling. A connected SessionLedger may
        # narrow it, but can never grant a capability the host did not opt in.
        return set(value) & set(self.grant_ceiling)

    def _require(self, permission: str) -> set[str]:
        current = self._permissions()
        if permission not in current:
            raise ExecutionContractError("PERMISSION_DENIED", f"project action requires {permission}")
        return current

    def _normalize_policy(self, raw: Any, *, grant_ceiling: set[str]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", "policy must be an object")
        # Only permissions and scheduler timeout caps are currently enforced
        # end to end. Resource and data-policy records would be promises with
        # no downstream enforcement, so reject them rather than persist them.
        allowed = {"permissions", "timeouts"}
        if set(raw) - allowed:
            raise ExecutionContractError("INVALID_REQUEST", "policy contains unsupported fields")
        _reject_sensitive_fields(raw, field="policy")
        permissions = raw.get("permissions", sorted(self._permissions()))
        if not isinstance(permissions, (list, tuple)) or any(not isinstance(item, str) for item in permissions):
            raise ExecutionContractError("INVALID_REQUEST", "policy.permissions must be a list of permission names")
        if len(set(permissions)) != len(permissions) or any(item not in PROJECT_PERMISSIONS for item in permissions):
            raise ExecutionContractError("INVALID_REQUEST", "policy.permissions contains a duplicate or unsupported permission")
        if not set(permissions) <= grant_ceiling:
            raise ExecutionContractError("POLICY_ESCALATION_REFUSED", "requested permissions exceed trusted host authority")
        result: dict[str, Any] = {"permissions": sorted(permissions)}
        values = raw.get("timeouts", {})
        if not isinstance(values, Mapping) or set(values) - {"queue_timeout_s", "execution_timeout_s"}:
            raise ExecutionContractError("INVALID_REQUEST", "policy.timeouts contains an unsupported field")
        checked: dict[str, int | float] = {}
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ExecutionContractError("INVALID_REQUEST", f"policy.timeouts.{name} must be non-negative and finite")
            checked[name] = value
        result["timeouts"] = checked
        _canonical_json(result, field="policy")
        return result

    def _resolve_workspace(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw or any(ord(char) < 32 for char in raw):
            raise ExecutionContractError("INVALID_REQUEST", "workspace must be a nonempty path")
        requested = Path(raw)
        if ".." in requested.parts:
            raise ExecutionContractError("PROJECT_WORKSPACE_OUTSIDE_ROOT", "workspace path traversal is forbidden")
        candidate = requested if requested.is_absolute() else self.workspace_root / requested
        if not candidate.is_absolute() or not _is_within(candidate, self.workspace_root):
            raise ExecutionContractError("PROJECT_WORKSPACE_OUTSIDE_ROOT", "workspace must be named inside the authorized root")
        cursor = self.workspace_root
        for part in candidate.relative_to(self.workspace_root).parts:
            cursor = cursor / part
            try:
                info = cursor.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ExecutionContractError("PROJECT_WORKSPACE_UNKNOWN", "workspace path could not be inspected") from exc
            if stat.S_ISLNK(info.st_mode):
                raise ExecutionContractError("PROJECT_WORKSPACE_SYMLINK_REFUSED", "workspace path cannot contain symlinks")
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExecutionContractError("PROJECT_WORKSPACE_INVALID", "workspace path cannot be resolved") from exc
        if resolved != candidate or not _is_within(resolved, self.workspace_root) or resolved == self.workspace_root:
            raise ExecutionContractError("PROJECT_WORKSPACE_OUTSIDE_ROOT", "workspace path is aliased or outside the authorized root")
        if resolved.exists():
            raise ExecutionContractError("PROJECT_WORKSPACE_EXISTS", "project workspace must not already exist")
        return resolved

    def _create_workspace(self, path: Path) -> list[Path]:
        created: list[Path] = []
        relative = path.relative_to(self.workspace_root)
        cursor = self.workspace_root
        try:
            for part in relative.parts[:-1]:
                cursor = cursor / part
                try:
                    info = cursor.lstat()
                except FileNotFoundError:
                    os.mkdir(cursor, 0o700)
                    created.append(cursor)
                    info = cursor.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise ExecutionContractError("PROJECT_WORKSPACE_SYMLINK_REFUSED", "workspace ancestor is not a real directory")
                if not _is_within(cursor.resolve(strict=True), self.workspace_root):
                    raise ExecutionContractError("PROJECT_WORKSPACE_OUTSIDE_ROOT", "workspace ancestor escaped the authorized root")
            os.mkdir(path, 0o700)
            created.append(path)
            if path.resolve(strict=True) != path or not _is_within(path.resolve(strict=True), self.workspace_root):
                raise ExecutionContractError("PROJECT_WORKSPACE_OUTSIDE_ROOT", "created workspace identity changed during creation")
            return created
        except BaseException:
            self._remove_created_empty_dirs(created)
            raise

    @staticmethod
    def _remove_created_empty_dirs(created: list[Path]) -> None:
        for path in reversed(created):
            try:
                path.rmdir()
            except OSError:
                # A nonempty path is left untouched; the caller never removes
                # data that appeared after this operation created the folder.
                pass

    def _record(self, project_id: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT project_id,workspace,schema_version,revision,record_json,created_at,updated_at "
                "FROM projects WHERE project_id=?", (project_id,),
            ).fetchone()
        if row is None:
            raise ExecutionContractError("PROJECT_NOT_FOUND", "project does not exist")
        if row["schema_version"] != PROJECT_SCHEMA_VERSION:
            raise ExecutionContractError("PROJECT_SCHEMA_UNSUPPORTED", "project record schema is unsupported")
        try:
            record = json.loads(row["record_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExecutionContractError("PROJECT_STATE_UNKNOWN", "persisted project record is malformed") from exc
        if (not isinstance(record, dict) or record.get("schema_version") != PROJECT_SCHEMA_VERSION
                or record.get("project_id") != row["project_id"] or record.get("workspace") != row["workspace"]
                or record.get("revision") != row["revision"]):
            raise ExecutionContractError("PROJECT_STATE_UNKNOWN", "project row and record identity do not match")
        self._verify_workspace_binding(record.get("workspace"))
        return record

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Return a verified persisted project record for an internal gate."""
        if not isinstance(project_id, str) or not project_id or len(project_id) > 128:
            raise ExecutionContractError("INVALID_REQUEST", "project_id must be a nonempty string")
        return self._record(project_id)

    def authorize_operation(self, project_id: str, permission: str) -> dict[str, Any]:
        """Require both the live host/service grant and project grant."""
        record = self.get_project(project_id)
        if permission not in PROJECT_PERMISSIONS:
            raise ExecutionContractError("PROJECT_SCOPE_UNSUPPORTED", "operation effect has no project permission mapping")
        current = self._permissions()
        configured = record.get("policy", {}).get("permissions")
        if not isinstance(configured, list) or any(item not in PROJECT_PERMISSIONS for item in configured):
            raise ExecutionContractError("PROJECT_STATE_UNKNOWN", "persisted project permissions are malformed")
        if permission not in current or permission not in configured:
            raise ExecutionContractError("PERMISSION_DENIED", f"project-scoped action requires {permission}")
        return record

    def apply_timeout_caps(self, project_id: str, timeouts: dict[str, Any]) -> dict[str, Any]:
        """Clamp request deadlines to the project's scheduler timeout caps."""
        record = self.get_project(project_id)
        policy = record.get("policy", {})
        caps = policy.get("timeouts", {}) if isinstance(policy, Mapping) else {}
        if not isinstance(caps, Mapping) or set(caps) - {"queue_timeout_s", "execution_timeout_s"}:
            raise ExecutionContractError("PROJECT_STATE_UNKNOWN", "persisted project timeout policy is malformed")
        effective = dict(timeouts)
        for key, cap in caps.items():
            if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap < 0:
                raise ExecutionContractError("PROJECT_STATE_UNKNOWN", "persisted project timeout policy is malformed")
            requested = effective.get(key)
            if requested is None or requested > cap:
                effective[key] = cap
        return effective

    def _verify_workspace_binding(self, raw_path: Any) -> None:
        if not isinstance(raw_path, str) or not raw_path:
            raise ExecutionContractError("PROJECT_WORKSPACE_IDENTITY_UNKNOWN", "persisted workspace identity is malformed")
        path = Path(raw_path)
        if not path.is_absolute() or not _is_within(path, self.workspace_root) or path == self.workspace_root:
            raise ExecutionContractError("PROJECT_WORKSPACE_IDENTITY_MISMATCH", "persisted workspace is outside the authorized root")
        try:
            resolved = path.resolve(strict=True)
            if resolved != path or not path.is_dir():
                raise ExecutionContractError("PROJECT_WORKSPACE_IDENTITY_MISMATCH", "persisted workspace is missing or aliased")
            cursor = self.workspace_root
            for part in path.relative_to(self.workspace_root).parts:
                cursor = cursor / part
                if stat.S_ISLNK(cursor.lstat().st_mode):
                    raise ExecutionContractError("PROJECT_WORKSPACE_IDENTITY_MISMATCH", "persisted workspace contains a symlink")
        except ExecutionContractError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExecutionContractError("PROJECT_WORKSPACE_IDENTITY_UNKNOWN", "persisted workspace could not be verified") from exc

    def _save_record(self, record: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        updated = dict(record)
        updated["revision"] = expected_revision + 1
        updated["updated_at"] = _now()
        text = _canonical_json(updated, field="project record")
        cursor = self.store.db.execute(
            "UPDATE projects SET revision=?,record_json=?,updated_at=? WHERE project_id=? AND revision=?",
            (updated["revision"], text, updated["updated_at"], updated["project_id"], expected_revision),
        )
        if cursor.rowcount != 1:
            raise ExecutionContractError("PROJECT_REVISION_CONFLICT", "project changed during update")
        return updated

    @staticmethod
    def _check_fields(arguments: Mapping[str, Any], allowed: set[str], required: set[str]) -> None:
        extra = set(arguments) - allowed
        missing = required - set(arguments)
        if extra:
            raise ExecutionContractError("INVALID_REQUEST", f"unsupported project argument(s): {', '.join(sorted(extra))}")
        if missing:
            raise ExecutionContractError("INVALID_REQUEST", f"missing project argument(s): {', '.join(sorted(missing))}")

    def dispatch(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one project operation after the outer durable admission.

        The shared ControlDaemon must perform catalog validation, durable
        idempotency admission, effect authorization, serialized queueing, and
        result persistence before/after calling this method.
        """
        if operation not in PROJECT_OPERATIONS:
            raise ExecutionContractError("UNSUPPORTED_OPERATION", "project action is not supported")
        if not isinstance(arguments, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", "project arguments must be an object")
        if operation == "project.create":
            return self._create(arguments)
        if operation == "project.inspect":
            return self._inspect(arguments)
        if operation == "project.contract_set":
            return self._contract_set(arguments)
        if operation == "project.policy_set":
            return self._policy_set(arguments)
        if operation == "project.permissions":
            return self._permissions_action(arguments)
        return self._state_export(arguments)

    def _create(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._check_fields(args, {"label", "workspace", "policy", "idempotency_key", "request_id"}, {"label", "workspace", "policy"})
        actor = self._require("project_write")
        label = args["label"]
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ExecutionContractError("INVALID_REQUEST", "label must contain 1 to 200 characters")
        path = self._resolve_workspace(args["workspace"])
        grant_ceiling = self._permissions() & actor
        policy = self._normalize_policy(args["policy"], grant_ceiling=grant_ceiling)
        project_id = str(uuid4())
        now = _now()
        record = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "project_id": project_id,
            "label": label.strip(),
            "workspace": str(path),
            "revision": 1,
            "contract": {},
            "policy": policy,
            "policy_change_log": [],
            "created_at": now,
            "updated_at": now,
        }
        record_json = _canonical_json(record, field="project record")
        with self.store.lock:
            db = self.store.db
            db.execute("BEGIN IMMEDIATE")
            created: list[Path] = []
            try:
                collision = db.execute("SELECT project_id FROM projects WHERE workspace=?", (str(path),)).fetchone()
                if collision:
                    raise ExecutionContractError("PROJECT_WORKSPACE_REGISTERED", "workspace already belongs to a project")
                created = self._create_workspace(path)
                db.execute(
                    "INSERT INTO projects(project_id,workspace,schema_version,revision,record_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (project_id, str(path), PROJECT_SCHEMA_VERSION, 1, record_json, now, now),
                )
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                self._remove_created_empty_dirs(created)
                raise
        return {"success": True, "data": {"project": record}}

    def _project_args(self, args: Mapping[str, Any], extra: set[str] | None = None) -> dict[str, Any]:
        allowed = {"project_id", "request_id"} | (extra or set())
        self._check_fields(args, allowed, {"project_id"})
        project_id = args["project_id"]
        if not isinstance(project_id, str) or not project_id or len(project_id) > 128:
            raise ExecutionContractError("INVALID_REQUEST", "project_id must be a nonempty string")
        return self._record(project_id)

    def _inspect(self, args: Mapping[str, Any]) -> dict[str, Any]:
        actor = self._require("inspect")
        record = self._project_args(args)
        self._require_project_permission(record, "inspect", actor)
        permissions = self._effective_project_permissions(record)
        return {"success": True, "data": {"project": record, "effective_permissions": permissions}}

    def _contract_set(self, args: Mapping[str, Any]) -> dict[str, Any]:
        actor = self._require("project_write")
        self._check_fields(args, {"project_id", "contract", "idempotency_key", "request_id"}, {"project_id", "contract"})
        record = self._record(str(args["project_id"]))
        self._require_project_permission(record, "project_write", actor)
        contract = args["contract"]
        if not isinstance(contract, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", "contract must be an object")
        _reject_sensitive_fields(contract, field="contract")
        _canonical_json(contract, field="contract")
        with self.store.lock:
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                current = self._record(record["project_id"])
                if current["revision"] != record["revision"]:
                    raise ExecutionContractError("PROJECT_REVISION_CONFLICT", "project changed during update")
                current["contract"] = json.loads(_canonical_json(contract, field="contract"))
                updated = self._save_record(current, record["revision"])
                self.store.db.execute("COMMIT")
            except BaseException:
                if self.store.db.in_transaction:
                    self.store.db.execute("ROLLBACK")
                raise
        return {"success": True, "data": {"project_id": updated["project_id"], "revision": updated["revision"], "contract_sha256": hashlib.sha256(_canonical_json(updated["contract"], field="contract").encode("utf-8")).hexdigest()}}

    def _policy_set(self, args: Mapping[str, Any]) -> dict[str, Any]:
        actor = self._require("host_control")
        self._check_fields(args, {"project_id", "policy", "authorization_ref", "idempotency_key", "request_id"}, {"project_id", "policy", "authorization_ref"})
        record = self._record(str(args["project_id"]))
        authorization_ref = args["authorization_ref"]
        if (not isinstance(authorization_ref, str) or not authorization_ref.strip() or len(authorization_ref) > 512
                or any(ord(char) < 32 for char in authorization_ref)):
            raise ExecutionContractError("AUTHORIZATION_REQUIRED", "project.policy_set requires a valid authorization reference")
        # Validate the requested shape and ceiling before consulting an
        # authorization provider.  The request never supplies its own ceiling.
        policy = self._normalize_policy(args["policy"], grant_ceiling=self._permissions())
        verifier = self.authorization_verifier
        try:
            # host_control is the authority. The reference is an audit handle;
            # an optional trusted verifier may further constrain its use.
            authorized = verifier(record["project_id"], authorization_ref) if verifier is not None else True
        except Exception as exc:
            raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "policy authorization could not be verified") from exc
        if authorized is not True:
            raise ExecutionContractError("AUTHORIZATION_REQUIRED", "policy authorization was not accepted")
        auth_digest = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()
        with self.store.lock:
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                current = self._record(record["project_id"])
                if current["revision"] != record["revision"]:
                    raise ExecutionContractError("PROJECT_REVISION_CONFLICT", "project changed during update")
                revision = record["revision"] + 1
                current["policy"] = policy
                current["policy_change_log"] = [*current.get("policy_change_log", []), {
                    "revision": revision, "authorization_ref_sha256": auth_digest, "at": _now(),
                }][-64:]
                updated = self._save_record(current, record["revision"])
                self.store.db.execute("COMMIT")
            except BaseException:
                if self.store.db.in_transaction:
                    self.store.db.execute("ROLLBACK")
                raise
        return {"success": True, "data": {"project_id": updated["project_id"], "revision": updated["revision"], "authorization_ref_sha256": auth_digest}}

    def _effective_project_permissions(self, record: Mapping[str, Any]) -> list[str]:
        current = self._permissions()
        permitted = record.get("policy", {}).get("permissions", [])
        if not isinstance(permitted, list) or any(not isinstance(item, str) for item in permitted):
            raise ExecutionContractError("PROJECT_STATE_UNKNOWN", "persisted project permissions are malformed")
        return sorted(set(permitted) & current)

    def _require_project_permission(self, record: Mapping[str, Any], permission: str, actor: set[str]) -> None:
        configured = record.get("policy", {}).get("permissions", [])
        if permission not in actor or permission not in configured:
            raise ExecutionContractError("PERMISSION_DENIED", f"project policy does not grant {permission}")

    def _permissions_action(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._require("inspect")
        record = self._project_args(args)
        return {"success": True, "data": {
            "project_id": record["project_id"],
            "configured_permissions": list(record["policy"].get("permissions", [])),
            "effective_permissions": self._effective_project_permissions(record),
            "credential_values_returned": False,
        }}

    @staticmethod
    def _project_id_in_metadata(metadata: Any, project_id: str) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("project_id") == project_id:
            return True
        for key in ("arguments", "execution"):
            nested = metadata.get(key)
            if isinstance(nested, Mapping) and nested.get("project_id") == project_id:
                return True
        return False

    def _state_export(self, args: Mapping[str, Any]) -> dict[str, Any]:
        actor = self._require("inspect")
        record = self._project_args(args, {"detail"})
        self._require_project_permission(record, "inspect", actor)
        detail = args.get("detail", "summary")
        if not isinstance(detail, str) or detail != "summary":
            raise ExecutionContractError("INVALID_REQUEST", "state export currently supports summary detail only")
        project_id = record["project_id"]
        jobs: list[dict[str, Any]] = []
        model_refs: dict[str, dict[str, Any]] = {}
        unattributed_revisions = 0
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT j.job_id,j.status,j.created_at,j.started_at,j.finished_at,o.operation,o.metadata "
                "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id ORDER BY j.created_at,j.job_id"
            ).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row["metadata"] or "{}")
                except json.JSONDecodeError:
                    continue
                if not self._project_id_in_metadata(metadata, project_id):
                    continue
                jobs.append({key: row[key] for key in ("job_id", "operation", "status", "created_at", "started_at", "finished_at")})
                execution = metadata.get("execution", {})
                raw_ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
                self._remember_model_ref(model_refs, raw_ref, execution.get("expected_revision") if isinstance(execution, Mapping) else None)
            revisions = self.store.db.execute("SELECT metadata FROM revisions").fetchall()
            for row in revisions:
                try:
                    metadata = json.loads(row[0])
                except json.JSONDecodeError:
                    unattributed_revisions += 1
                    continue
                if not isinstance(metadata, Mapping):
                    unattributed_revisions += 1
                    continue
                bound_project = metadata.get("project_id")
                if bound_project == project_id:
                    pass
                elif bound_project is None or metadata.get("attribution") == "UNATTRIBUTED":
                    unattributed_revisions += 1
                    continue
                else:
                    continue
                self._remember_model_ref(model_refs, metadata.get("model_ref"), metadata.get("revision"))
        contract_json = _canonical_json(record.get("contract", {}), field="contract")
        data = {
            "project_id": project_id,
            "project_revision": record["revision"],
            "contract_sha256": hashlib.sha256(contract_json.encode("utf-8")).hexdigest(),
            "models": sorted(model_refs.values(), key=lambda item: json.dumps(item["model_ref"], sort_keys=True)),
            "jobs": jobs,
            "active_or_unknown_jobs": [job["job_id"] for job in jobs if job["status"] in {"QUEUED", "RUNNING", "UNKNOWN", "RECONCILING"}],
            "model_attribution": "PARTIAL" if unattributed_revisions else "PROJECT_TAGGED_ROWS_ONLY",
            "unattributed_revision_rows": unattributed_revisions,
            "scope": "persisted_records_only; no live engine query",
            "credential_values_returned": False,
        }
        return {"success": True, "data": data}

    @staticmethod
    def _remember_model_ref(target: dict[str, dict[str, Any]], raw_ref: Any, revision: Any) -> None:
        if not isinstance(raw_ref, Mapping):
            return
        names = ("schema_version", "session_id", "server_instance_id", "model_tag", "generation")
        ref = {name: raw_ref[name] for name in names if name in raw_ref}
        if (set(ref) != set(names) or isinstance(ref["schema_version"], bool) or not isinstance(ref["schema_version"], int)
                or any(not isinstance(ref[name], str) or not ref[name] for name in ("session_id", "server_instance_id", "model_tag"))
                or isinstance(ref["generation"], bool) or not isinstance(ref["generation"], int)):
            return
        key = json.dumps(ref, sort_keys=True, separators=(",", ":"))
        value = {"model_ref": ref}
        if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
            value["revision"] = revision
        target[key] = value
