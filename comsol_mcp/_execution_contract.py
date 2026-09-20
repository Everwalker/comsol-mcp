"""Small, fail-closed execution contract used by the W05 control-plane.

This module deliberately has no MPh/JPype dependency.  It is the boundary
between an MCP request and a future serialized engine dispatcher: identity,
revision, permission, and path decisions are made before an engine call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


MODEL_REF_SCHEMA_VERSION = 1
_TRACKING_KEYS = frozenset({"request_id", "correlation_id", "trace_id", "span_id"})
_RPC_WAIT_KEYS = frozenset({"rpc_timeout_s", "rpc_wait_timeout_s", "transport_timeout_s"})


class ExecutionContractError(RuntimeError):
    """A structured, non-engine error that a MCP adapter can expose safely."""

    def __init__(self, code: str, message: str, *, safe_retry: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_retry = safe_retry

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "safe_retry": self.safe_retry}


@dataclass(frozen=True, slots=True)
class ModelRef:
    """Server identity only; labels and filesystem paths are deliberately absent."""

    session_id: str
    server_instance_id: str
    model_tag: str
    generation: int
    schema_version: int = MODEL_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_REF_SCHEMA_VERSION:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "unsupported model_ref schema")
        if not all(isinstance(value, str) and value for value in (self.session_id, self.server_instance_id, self.model_tag)):
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref identity fields must be non-empty strings")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 1:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref generation must be a positive integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "server_instance_id": self.server_instance_id,
            "model_tag": self.model_tag,
            "generation": self.generation,
        }


def model_ref_from_mapping(value: Mapping[str, Any]) -> ModelRef:
    """Decode a wire model_ref without admitting label/path pseudo-identities."""
    allowed = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
    unexpected = set(value) - allowed
    if unexpected:
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref contains non-identity fields")
    try:
        return ModelRef(
            session_id=value["session_id"],
            server_instance_id=value["server_instance_id"],
            model_tag=value["model_tag"],
            generation=value["generation"],
            schema_version=value.get("schema_version", MODEL_REF_SCHEMA_VERSION),
        )
    except KeyError as exc:
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref is missing a required identity field") from exc


def _canonical(value: Any) -> Any:
    if isinstance(value, ModelRef):
        return _canonical(value.as_dict())
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def canonical_request_hash(
    operation: str,
    arguments: Mapping[str, Any] | None,
    model_ref: ModelRef | Mapping[str, Any] | None,
    expected_revision: int | None,
    **request_fields: Any,
) -> str:
    """Hash semantic request content, excluding tracing and caller wait budgets."""
    if not isinstance(operation, str) or not operation:
        raise ExecutionContractError("INVALID_REQUEST", "operation must be a non-empty string")
    ignored = _TRACKING_KEYS | _RPC_WAIT_KEYS
    extra = {key: value for key, value in request_fields.items() if key not in ignored}
    payload = {
        "operation": operation,
        "arguments": dict(arguments or {}),
        "model_ref": model_ref.as_dict() if isinstance(model_ref, ModelRef) else model_ref,
        "expected_revision": expected_revision,
        **extra,
    }
    encoded = json.dumps(_canonical(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


# Current legacy surface.  Every entry receives an effect decision; unlisted
# tools fail closed, including future api-invoke methods with a claimed effect.
LEGACY_TOOL_EFFECTS: dict[str, str] = {
    **{name: "inspect" for name in (
        "server_info", "check_server_port", "workflow_info", "visible_main_workflow_status",
        "verify_visible_main_session", "mcp_tool_audit", "model_tree", "get_parameters",
        "list_physics", "list_physics_features", "list_solver_config", "list_solver_features",
        "run_study_status",
    )},
    **{name: "project_write" for name in (
        "configure_single_main_workflow", "load_visible_main_model", "unlock_visible_main",
        "load_current_main_model", "server_connect", "server_disconnect", "model_create", "model_load",
        "prune_loaded_models", "set_parameters", "ensure_component", "ensure_geometry", "ensure_mesh",
        "create_feature", "update_feature", "delete_feature", "create_physics",
        "remove_physics", "create_physics_feature", "update_physics_feature", "remove_physics_feature",
        "set_physics_selection", "manage_variables", "create_solver_config", "configure_solver",
    )},
    **{name: "evaluate" for name in ("evaluate_expressions", "get_core_metrics")},
    **{name: "compute" for name in ("run_feature", "run_study", "run_visible_main_iteration", "run_study_async", "runtime_poc_v64")},
    **{name: "file_write" for name in ("save_main_model_snapshot", "commit_current_main_model", "save_model")},
    "start_visible_main_workflow": "project_write",
    "start_visible_main_workflow_async": "compute",
    "server_start": "host_control",
    # G2 registry/model operations.  These names are also used by the
    # operation fallback, so they must pass the same server-side effect
    # classification as a directly published tool.
    **{name: "inspect" for name in (
        "registry_list", "registry_describe", "registry_search", "registry_manifest",
        "node_inspect", "node_children", "node_find", "node_property_schema", "node_property_get",
        "code_describe_java", "code_inspect_run", "checkpoint_list", "checkpoint_inspect", "checkpoint_diff",
        "transaction_preview", "transaction_verify", "docs_search", "docs_get", "docs_examples", "docs_error_search",
    )},
    **{name: "project_write" for name in (
        "node_property_set", "node_property_index_set", "node_property_entry_set",
        "checkpoint_create", "checkpoint_restore", "transaction_apply", "transaction_recover", "docs_index",
    )},
    "transaction_trial": "compute",
    "code_compile_java": "compute",
    "code_execute_java": "trusted_code",
    # The outer fallback is only a registry dispatch envelope.  Its nested
    # operation is classified again by the server-side registry before it can
    # enter the model execution path.
    "registry_call": "inspect",
    "operation_describe": "inspect",
    "operation_call": "inspect",
}

EFFECT_PERMISSIONS = {
    "inspect": "inspect",
    "project_write": "project_write",
    "compute": "compute",
    "evaluate": "project_write",
    "file_write": "project_write",
    "state_write": "project_write",
    "trusted_code": "trusted_code",
    "host_control": "host_control",
}


def permission_for_effect(effect: str, *, declared_effect: str | None = None) -> str:
    """Resolve a registry effect; caller-declared dynamic effects are never trusted."""
    if effect == "dynamic":
        raise ExecutionContractError("PERMISSION_DENIED", "dynamic effect requires a server-side registry decision")
    permission = EFFECT_PERMISSIONS.get(effect)
    if permission is None:
        raise ExecutionContractError("PERMISSION_DENIED", f"unclassified or unsafe effect: {effect!r}")
    return permission


#: G3 (W13-W16) catalogue effect -> legacy effect classification.  The
#: catalogue stays the single source of truth (``_g3_ops.EFFECTS``); unknown
#: catalogue effects are refused rather than defaulted.  ``DYNAMIC`` maps to
#: the write class: the server-side decision for the dynamic G3 operations in
#: this round is "mutating", and that is the strict path.
_G3_CATALOG_EFFECTS: dict[str, str] = {
    "READ": "inspect",
    "WRITE": "project_write",
    "STATE_WRITE": "state_write",
    "FILE_WRITE": "file_write",
    "EVALUATE": "evaluate",
    "COMPUTE": "compute",
    "TRUSTED_CODE": "trusted_code",
    "DYNAMIC": "project_write",
}


def _g3_legacy_effect(tool_name: str) -> str | None:
    """Classify a G3 domain-operation alias through its catalogue effect."""
    try:
        from ._g3_ops import EFFECTS
    except Exception:
        return None
    for operation_id, catalog_effect in EFFECTS.items():
        if operation_id.replace(".", "_") == tool_name:
            return _G3_CATALOG_EFFECTS.get(str(catalog_effect).upper())
    return None


def permission_for_legacy_tool(tool_name: str) -> str:
    effect = LEGACY_TOOL_EFFECTS.get(tool_name)
    if effect is None:
        effect = _g3_legacy_effect(tool_name)
    if effect is None:
        raise ExecutionContractError("PERMISSION_DENIED", f"legacy tool has no effect classification: {tool_name}")
    return permission_for_effect(effect)


def canonical_project_path(project_root: str | Path, requested: str | Path) -> Path:
    """Resolve symlinks and reject a path outside the approved project root."""
    root = Path(project_root).expanduser().resolve(strict=False)
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ExecutionContractError("PERMISSION_DENIED", "path escapes the approved project root") from exc
    relative_parts = resolved.relative_to(root).parts
    if any(part in {".phase1-private", ".phase2-private", "control-private"} for part in relative_parts):
        raise ExecutionContractError("PERMISSION_DENIED", "path enters a private credential or control directory")
    return resolved


def redact_credentials_ref(credentials_ref: str | None) -> dict[str, Any]:
    """Return only presence metadata: refs often encode a secret path or token."""
    return {"credentials_configured": bool(credentials_ref)}


@dataclass(slots=True)
class _ModelState:
    ref: ModelRef
    revision: int = 0
    fingerprint: str | None = None
    external_event_counter: int = 0
    observed_external_event_counter: int = 0
    dirty: bool = False
    retired: bool = False
    active_operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionTicket:
    operation_id: str
    request_id: str
    request_hash: str
    operation: str
    model_ref: ModelRef
    expected_revision: int
    external_event_counter: int
    fingerprint: str | None


@dataclass(slots=True)
class SessionLedger:
    """In-memory contract state; W07 is responsible for durable persistence."""

    session_id: str
    server_instance_id: str
    server_ownership: str = "shared"
    model_ownership: dict[str, str] = field(default_factory=dict)
    permissions: set[str] = field(default_factory=lambda: {"inspect", "project_write", "compute"})
    _generations: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _models: dict[str, _ModelState] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.server_ownership not in {"shared", "mcp_managed", "user_owned"}:
            raise ExecutionContractError("INVALID_REQUEST", "server_ownership must be shared, mcp_managed, or user_owned")
        if not self.session_id or not self.server_instance_id:
            raise ExecutionContractError("INVALID_REQUEST", "session and server identity are required")

    def bind_model(self, model_tag: str, *, ownership: str = "user_owned", fingerprint: str | None = None) -> ModelRef:
        if ownership not in {"user_owned", "mcp_owned", "shared"}:
            raise ExecutionContractError("INVALID_REQUEST", "invalid model ownership")
        generation = self._generations.get(model_tag, 0) + 1
        self._generations[model_tag] = generation
        ref = ModelRef(self.session_id, self.server_instance_id, model_tag, generation)
        self._models[model_tag] = _ModelState(ref=ref, fingerprint=fingerprint)
        self.model_ownership[model_tag] = ownership
        return ref

    def rebind_model(self, model_tag: str, *, fingerprint: str | None = None) -> ModelRef:
        ownership = self.model_ownership.get(model_tag, "user_owned")
        return self.bind_model(model_tag, ownership=ownership, fingerprint=fingerprint)

    def retire_model(self, model_ref: ModelRef) -> None:
        state = self._state_for(model_ref)
        state.retired = True
        self._generations[model_ref.model_tag] = max(self._generations.get(model_ref.model_tag, 0), model_ref.generation) + 1

    def observe_external_change(self, model_ref: ModelRef, *, fingerprint: str | None) -> None:
        state = self._state_for(model_ref)
        state.external_event_counter += 1
        if state.fingerprint != fingerprint:
            state.dirty = True

    def observe_engine_state(self, model_ref: ModelRef, *, external_event_counter: int, fingerprint: str | None) -> None:
        """Record a worker observation without treating it as reconciliation."""
        if isinstance(external_event_counter, bool) or not isinstance(external_event_counter, int) or external_event_counter < 0:
            raise ExecutionContractError("INVALID_REQUEST", "external_event_counter must be a non-negative integer")
        state = self._state_for(model_ref)
        if external_event_counter < state.external_event_counter:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "engine external event counter moved backwards")
        if external_event_counter != state.external_event_counter:
            state.external_event_counter = external_event_counter
            state.dirty = True
        if state.fingerprint != fingerprint:
            state.dirty = True

    def mark_external_observed(self, model_ref: ModelRef, *, fingerprint: str | None) -> None:
        """A non-atomic observation.  It cannot claim a COMSOL-side CAS."""
        state = self._state_for(model_ref)
        if state.observed_external_event_counter != state.external_event_counter or state.fingerprint != fingerprint:
            state.revision += 1
        state.observed_external_event_counter = state.external_event_counter
        state.fingerprint = fingerprint
        state.dirty = False

    def revision(self, model_ref: ModelRef) -> int:
        return self._state_for(model_ref).revision

    def begin_write(
        self,
        operation: str,
        arguments: Mapping[str, Any] | None,
        model_ref: ModelRef,
        expected_revision: int | None,
        *,
        effect: str = "project_write",
        request_id: str | None = None,
        fingerprint: str | None = None,
        **request_fields: Any,
    ) -> ExecutionTicket:
        permission = permission_for_effect(effect)
        if permission not in self.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", f"permission required: {permission}")
        if expected_revision is None:
            raise ExecutionContractError("REVISION_CONFLICT", "write requests require expected_revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ExecutionContractError("REVISION_CONFLICT", "expected_revision must be a non-negative integer")
        state = self._state_for(model_ref)
        if state.active_operation_id is not None:
            raise ExecutionContractError("ENGINE_BUSY", "a write is already active for this model")
        if state.dirty or state.external_event_counter != state.observed_external_event_counter:
            raise ExecutionContractError("REVISION_CONFLICT", "external model change requires reconciliation")
        if state.fingerprint != fingerprint:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "touched-node fingerprint changed")
        if state.revision != expected_revision:
            raise ExecutionContractError("REVISION_CONFLICT", "expected_revision does not match managed revision")
        request_id = request_id or str(uuid4())
        ticket = ExecutionTicket(
            operation_id=str(uuid4()), request_id=request_id,
            request_hash=canonical_request_hash(operation, arguments, model_ref, expected_revision, **request_fields),
            operation=operation, model_ref=model_ref, expected_revision=expected_revision,
            external_event_counter=state.external_event_counter, fingerprint=state.fingerprint,
        )
        state.active_operation_id = ticket.operation_id
        return ticket

    def finish(self, ticket: ExecutionTicket, *, outcome: str, changed: bool = False, fingerprint: str | None = None) -> dict[str, Any]:
        state = self._state_for(ticket.model_ref)
        if state.active_operation_id != ticket.operation_id:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "ticket is stale, already finished, or was not active")
        if outcome not in {"succeeded", "failed", "partial", "unknown"}:
            raise ExecutionContractError("INVALID_REQUEST", "unknown execution outcome")
        state.active_operation_id = None
        conflict = state.external_event_counter != ticket.external_event_counter or state.fingerprint != ticket.fingerprint
        if conflict and outcome == "succeeded":
            outcome = "unknown"
        unsafe = outcome in {"partial", "unknown"} or conflict
        mutation_possible = changed or unsafe
        if mutation_possible:
            state.revision += 1
            state.fingerprint = fingerprint
        state.dirty = unsafe
        return {
            "operation_id": ticket.operation_id,
            "outcome": outcome,
            "revision": state.revision,
            "dirty": state.dirty,
            "partial_change": outcome == "partial",
            "execution_state_unknown": outcome == "unknown",
            "safe_retry": outcome == "failed" and not mutation_possible and not conflict,
        }

    def _state_for(self, model_ref: ModelRef) -> _ModelState:
        if model_ref.session_id != self.session_id or model_ref.server_instance_id != self.server_instance_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref belongs to another session or server")
        state = self._models.get(model_ref.model_tag)
        if state is None or state.retired or state.ref != model_ref:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "stale or unbound model_ref")
        return state
