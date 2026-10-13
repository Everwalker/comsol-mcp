"""Durable F04 lifecycle records inside the existing OperationStore authority.

This module owns no database or parallel source of truth. Lifecycle state is a
validated nested namespace in ``sessions.metadata`` and is updated atomically
with preservation of the Worker-ledger fields in that same row.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping


LIFECYCLE_SCHEMA_VERSION = 1
SESSION_STATES = frozenset({
    "NEW", "STARTING", "CONNECTING", "CONNECTED", "DISCONNECTED",
    "STOPPING", "STOPPED", "LOST", "UNKNOWN",
})
CLIENT_STATES = frozenset({"UNKNOWN", "DISCONNECTED", "CONNECTED", "RETIRED"})
SERVER_STATES = frozenset({"UNKNOWN", "SHARED", "USER_OWNED", "MCP_MANAGED", "STOPPED"})
HEALTH_STATES = frozenset({"UNKNOWN", "HEALTHY", "UNHEALTHY", "STALE"})

_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "project_id", "session_id", "revision", "state",
    "runtime_id", "endpoint", "client_state", "server_state",
    "server_ownership", "worker_instance_id", "worker_epoch",
    "server_instance_id", "server_process_identity", "health",
    "created_at", "updated_at",
})
_ENDPOINT_KEYS = frozenset({"host", "port"})
_PROCESS_KEYS = frozenset({"pid", "birth", "executable", "runtime_id", "endpoint"})
_HEALTH_KEYS = frozenset({"status", "observed_at", "source"})


class SessionLifecycleError(RuntimeError):
    """Base class for fail-closed persisted lifecycle state errors."""


class SessionLifecycleMalformed(SessionLifecycleError):
    pass


class SessionLifecycleProjectConflict(SessionLifecycleError):
    pass


class SessionLifecycleRevisionConflict(SessionLifecycleError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _nonempty(value: Any, name: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or any(ord(char) < 32 for char in value):
        raise SessionLifecycleMalformed(f"{name} must be a nonempty bounded string")
    return value


def _optional_nonempty(value: Any, name: str, *, max_length: int = 512) -> str | None:
    return None if value is None else _nonempty(value, name, max_length=max_length)


def _validate_endpoint(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _ENDPOINT_KEYS:
        raise SessionLifecycleMalformed("endpoint must contain only host and port")
    host = _nonempty(value.get("host"), "endpoint.host", max_length=255)
    port = value.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise SessionLifecycleMalformed("endpoint.port must be an integer in range")
    return {"host": host, "port": port}


def _validate_process_identity(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _PROCESS_KEYS:
        raise SessionLifecycleMalformed("server process identity is incomplete")
    pid = value.get("pid")
    if type(pid) is not int or pid <= 0:
        raise SessionLifecycleMalformed("server process PID is invalid")
    birth = _nonempty(value.get("birth"), "server process birth")
    executable = _nonempty(value.get("executable"), "server process executable", max_length=4096)
    runtime_id = _nonempty(value.get("runtime_id"), "server process runtime_id")
    endpoint = _validate_endpoint(value.get("endpoint"))
    if endpoint is None:
        raise SessionLifecycleMalformed("server process endpoint is required")
    return {
        "pid": pid,
        "birth": birth,
        "executable": executable,
        "runtime_id": runtime_id,
        "endpoint": endpoint,
    }


def _validate_health(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _HEALTH_KEYS:
        raise SessionLifecycleMalformed("health must contain status, observed_at, and source")
    status = value.get("status")
    if not isinstance(status, str) or status not in HEALTH_STATES:
        raise SessionLifecycleMalformed("health status is invalid")
    observed_at = _optional_nonempty(value.get("observed_at"), "health.observed_at")
    source = _optional_nonempty(value.get("source"), "health.source")
    if status in {"HEALTHY", "UNHEALTHY"} and (observed_at is None or source is None):
        raise SessionLifecycleMalformed("live health requires an observation time and source")
    return {"status": status, "observed_at": observed_at, "source": source}


def validate_lifecycle_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return an exact, detached record; credentials and arbitrary fields fail closed."""
    if not isinstance(value, Mapping) or set(value) != _TOP_LEVEL_KEYS:
        raise SessionLifecycleMalformed("lifecycle record has missing or unsupported fields")
    if value.get("schema_version") != LIFECYCLE_SCHEMA_VERSION:
        raise SessionLifecycleMalformed("unsupported lifecycle schema version")
    project_id = _nonempty(value.get("project_id"), "project_id", max_length=128)
    session_id = _nonempty(value.get("session_id"), "session_id", max_length=128)
    revision = value.get("revision")
    if type(revision) is not int or revision < 1:
        raise SessionLifecycleMalformed("lifecycle revision must be positive")
    state = value.get("state")
    client_state = value.get("client_state")
    server_state = value.get("server_state")
    ownership = value.get("server_ownership")
    if (not isinstance(state, str) or state not in SESSION_STATES
            or not isinstance(client_state, str) or client_state not in CLIENT_STATES
            or not isinstance(server_state, str) or server_state not in SERVER_STATES):
        raise SessionLifecycleMalformed("lifecycle state is invalid")
    if not isinstance(ownership, str) or ownership not in {"unknown", "shared", "user_owned", "mcp_managed"}:
        raise SessionLifecycleMalformed("server ownership is invalid")
    if (server_state == "MCP_MANAGED") != (ownership == "mcp_managed"):
        raise SessionLifecycleMalformed("managed server state and ownership must agree")
    worker_epoch = value.get("worker_epoch")
    if worker_epoch is not None and (type(worker_epoch) is not int or worker_epoch < 1):
        raise SessionLifecycleMalformed("worker_epoch must be a positive integer or null")
    process_identity = _validate_process_identity(value.get("server_process_identity"))
    if ownership == "mcp_managed" and process_identity is None:
        raise SessionLifecycleMalformed("MCP-managed server requires exact process identity")
    if process_identity is not None:
        if process_identity["runtime_id"] != value.get("runtime_id"):
            raise SessionLifecycleMalformed("process and session runtime identities differ")
        if process_identity["endpoint"] != value.get("endpoint"):
            raise SessionLifecycleMalformed("process and session endpoints differ")
    for field in ("created_at", "updated_at"):
        _nonempty(value.get(field), field, max_length=64)
    return {
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        "project_id": project_id,
        "session_id": session_id,
        "revision": revision,
        "state": state,
        "runtime_id": _optional_nonempty(value.get("runtime_id"), "runtime_id"),
        "endpoint": _validate_endpoint(value.get("endpoint")),
        "client_state": client_state,
        "server_state": server_state,
        "server_ownership": ownership,
        "worker_instance_id": _optional_nonempty(value.get("worker_instance_id"), "worker_instance_id"),
        "worker_epoch": worker_epoch,
        "server_instance_id": _optional_nonempty(value.get("server_instance_id"), "server_instance_id"),
        "server_process_identity": process_identity,
        "health": _validate_health(value.get("health")),
        "created_at": value["created_at"],
        "updated_at": value["updated_at"],
    }


class SessionLifecycleStore:
    """Project-filtered lifecycle access over ``OperationStore.sessions``."""

    def __init__(self, operation_store: Any):
        if not all(callable(getattr(operation_store, name, None)) for name in (
            "get_metadata", "list_metadata", "update_session_lifecycle",
        )):
            raise TypeError("SessionLifecycleStore requires the existing OperationStore authority")
        self._store = operation_store

    def get(self, project_id: str, session_id: str) -> dict[str, Any] | None:
        _nonempty(project_id, "project_id", max_length=128)
        _nonempty(session_id, "session_id", max_length=128)
        metadata = self._store.get_metadata("sessions", session_id)
        if metadata is None:
            return None
        if not isinstance(metadata, dict):
            raise SessionLifecycleMalformed("persisted session row is malformed")
        raw = metadata.get("lifecycle")
        if raw is None:
            # Existing v1 runtime rows have no project attribution. They stay
            # explicitly un-attributed; no project is guessed on read.
            return None
        record = validate_lifecycle_record(raw)
        if record["session_id"] != session_id:
            raise SessionLifecycleMalformed("lifecycle row key does not match its session identity")
        if record["project_id"] != project_id:
            raise SessionLifecycleProjectConflict("session belongs to another project")
        return deepcopy(record)

    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        _nonempty(project_id, "project_id", max_length=128)
        found: list[dict[str, Any]] = []
        for metadata in self._store.list_metadata("sessions"):
            if not isinstance(metadata, dict):
                raise SessionLifecycleMalformed("persisted session row is malformed")
            raw = metadata.get("lifecycle")
            if raw is None:
                continue
            if not isinstance(raw, Mapping):
                raise SessionLifecycleMalformed("persisted lifecycle record is malformed")
            # Read the project discriminator before validating a foreign row,
            # avoiding disclosure of its other details across project scope.
            if raw.get("project_id") != project_id:
                continue
            record = validate_lifecycle_record(raw)
            if metadata.get("session_id", record["session_id"]) != record["session_id"]:
                raise SessionLifecycleMalformed("lifecycle row key does not match its session identity")
            found.append(record)
        return sorted(found, key=lambda item: (item["updated_at"], item["session_id"]))

    def save(self, record: Mapping[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
        proposed = validate_lifecycle_record(record)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError("expected_revision must be a positive integer")
        result: dict[str, Any] = {}

        def update(current: dict[str, Any] | None) -> dict[str, Any]:
            nonlocal result
            now = _timestamp()
            if current is None:
                if expected_revision is not None:
                    raise SessionLifecycleRevisionConflict("session lifecycle record no longer exists")
                if proposed["revision"] != 1:
                    raise SessionLifecycleRevisionConflict("new lifecycle record must start at revision 1")
                next_revision = 1
                created_at = proposed["created_at"]
            else:
                existing = validate_lifecycle_record(current)
                if existing["project_id"] != proposed["project_id"]:
                    raise SessionLifecycleProjectConflict("session is already bound to another project")
                if existing["session_id"] != proposed["session_id"]:
                    raise SessionLifecycleMalformed("session lifecycle key cannot change")
                if expected_revision is not None and existing["revision"] != expected_revision:
                    raise SessionLifecycleRevisionConflict("session lifecycle revision changed")
                next_revision = existing["revision"] + 1
                created_at = existing["created_at"]
            updated = dict(proposed)
            updated["revision"] = next_revision
            updated["created_at"] = created_at
            updated["updated_at"] = now
            result = validate_lifecycle_record(updated)
            return result

        persisted = self._store.update_session_lifecycle(proposed["session_id"], update)
        return deepcopy(result or validate_lifecycle_record(persisted))


def new_lifecycle_record(
    *, project_id: str, session_id: str, state: str = "NEW",
    runtime_id: str | None = None, endpoint: Mapping[str, Any] | None = None,
    client_state: str = "UNKNOWN", server_state: str = "UNKNOWN",
    server_ownership: str = "unknown", worker_instance_id: str | None = None,
    worker_epoch: int | None = None, server_instance_id: str | None = None,
    server_process_identity: Mapping[str, Any] | None = None,
    health: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a schema-checked initial/update candidate without secrets."""
    now = _timestamp()
    return validate_lifecycle_record({
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        "project_id": project_id,
        "session_id": session_id,
        "revision": 1,
        "state": state,
        "runtime_id": runtime_id,
        "endpoint": dict(endpoint) if endpoint is not None else None,
        "client_state": client_state,
        "server_state": server_state,
        "server_ownership": server_ownership,
        "worker_instance_id": worker_instance_id,
        "worker_epoch": worker_epoch,
        "server_instance_id": server_instance_id,
        "server_process_identity": dict(server_process_identity) if server_process_identity is not None else None,
        "health": dict(health) if health is not None else {"status": "UNKNOWN", "observed_at": None, "source": None},
        "created_at": now,
        "updated_at": now,
    })
