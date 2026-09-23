"""Authoritative G2 action registry and host fallback helpers.

The design catalog remains the source of operation names and schemas.  This
module loads it once, marks the bounded W08-W12 implementation surface, and
exposes deterministic list/describe/search/manifest results.  Unknown catalog
entries stay visible as unavailable metadata; they are never silently exposed
as executable operations.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, LEGACY_TOOL_EFFECTS


_WORKSPACE_CATALOG_PATH = Path(__file__).resolve().parents[1] / "docs" / "comsol_mcp_design_v1" / "02_ACTION_CATALOG.json"
_PACKAGE_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "g2" / "02_ACTION_CATALOG.json"


def _select_catalog_path() -> Path:
    """Select the immutable package copy and verify checkout provenance.

    The wheel must remain usable after the repository checkout is absent, so
    the package resource is authoritative whenever it exists.  In a checkout,
    compare it byte-for-byte with the reviewed design source and fail closed
    if the two copies drift.  The workspace fallback keeps older editable
    checkouts importable while the package-data migration is being completed.
    """
    package_path, workspace_path = _PACKAGE_CATALOG_PATH, _WORKSPACE_CATALOG_PATH
    if package_path.is_file():
        if workspace_path.is_file():
            try:
                package_bytes = package_path.read_bytes()
                workspace_bytes = workspace_path.read_bytes()
            except OSError as exc:
                raise RuntimeError("action catalog resource could not be read") from exc
            if package_bytes != workspace_bytes:
                raise RuntimeError(
                    "packaged action catalog differs from the reviewed design source: "
                    f"{package_path} != {workspace_path}"
                )
        return package_path
    if workspace_path.is_file():
        return workspace_path
    # Keep the missing-resource error in _catalog_entries(), where the path is
    # included without attempting to guess another checkout location.
    return package_path


# Installed wheels use this package resource even when a similarly named
# repository directory happens to be adjacent to site-packages.
CATALOG_PATH = _select_catalog_path()

# These are the G2 actions with a control-plane implementation in this
# repository.  The registry deliberately keeps the list narrower than the
# 272-action design catalog so an unimplemented action cannot be called by
# accident.  G3 (W13-W16) domain operations live in ``_g3_ops`` and extend
# this surface through ``is_implemented`` below.
IMPLEMENTED_OPERATIONS = frozenset({
    "registry.list", "registry.describe", "registry.search", "registry.call", "registry.manifest",
    "node.inspect", "node.children", "node.find", "node.property_schema", "node.property_get",
    "node.property_set", "node.property_index_set", "node.property_entry_set",
    "code.describe_java", "code.compile_java", "code.execute_java", "code.inspect_run",
    "checkpoint.create", "checkpoint.list", "checkpoint.inspect", "checkpoint.restore",
    "transaction.preview", "transaction.trial", "transaction.apply", "transaction.verify", "transaction.recover",
    "docs.index", "docs.search", "docs.get", "docs.examples", "docs.error_search",
})


def is_implemented(operation_id: str) -> bool:
    """True when the operation has a control-plane implementation in this build.

    G2 keeps a frozen allow-list; the G3 domain modules register their own
    executable set in ``_g3_ops``.  The import is lazy so this module stays
    importable while G2-only deployments exist and to avoid an import cycle
    (``_g3_ops`` reads the catalog through this module).
    """
    if operation_id in IMPLEMENTED_OPERATIONS:
        return True
    try:
        from ._g3_ops import IMPLEMENTED_OPERATIONS as g3_implemented
    except Exception:
        return False
    return operation_id in g3_implemented

MCP_ALIASES = {
    "registry.list": "registry_list",
    "registry.describe": "registry_describe",
    "registry.search": "registry_search",
    "registry.call": "registry_call",
    "registry.manifest": "registry_manifest",
}

# The fallback is an explicit compatibility map for the original 51-tool
# surface.  It is intentionally separate from the 272-action design catalog:
# hiding a legacy tool from ``tools/list`` must not remove its managed backend
# route, and a fallback call still enters the ordinary service permission and
# revision gate.  ``runtime_poc_v64`` remains listed for truthful discovery;
# the backend may report its existing disabled/unsupported result.
LEGACY_FALLBACK_NAMES = frozenset({
    "check_server_port", "commit_current_main_model", "configure_single_main_workflow", "configure_solver",
    "create_feature", "create_physics", "create_physics_feature", "create_solver_config", "delete_feature",
    "ensure_component", "ensure_geometry", "ensure_mesh", "evaluate_expressions", "get_core_metrics",
    "get_parameters", "list_physics", "list_physics_features", "list_solver_config", "list_solver_features",
    "load_current_main_model", "load_visible_main_model", "manage_variables", "mcp_tool_audit", "model_create",
    "model_load", "model_tree", "prune_loaded_models", "remove_physics", "remove_physics_feature", "run_feature",
    "run_study", "run_study_async", "run_study_status", "run_visible_main_iteration", "runtime_poc_v64",
    "save_main_model_snapshot", "save_model", "server_connect", "server_disconnect", "server_info", "server_start",
    "set_parameters", "set_physics_selection", "start_visible_main_workflow", "start_visible_main_workflow_async",
    "unlock_visible_main", "update_feature", "update_physics_feature", "verify_visible_main_session",
    "visible_main_workflow_status", "workflow_info",
})

# FastMCP already owns the public legacy argument contract.  Keep one lazy,
# read-only snapshot of those schemas for the operation fallback instead of
# maintaining a second hand-written table for 51 functions.  The imports below
# load function definitions and register them with a disposable FastMCP object;
# no tool body, COMSOL client, JVM, or engine operation is invoked.
_LEGACY_SCHEMA_CACHE: dict[str, dict[str, Any]] | None = None


def _legacy_input_schemas() -> dict[str, dict[str, Any]]:
    """Return strict schemas generated by the existing FastMCP registrations."""
    global _LEGACY_SCHEMA_CACHE
    if _LEGACY_SCHEMA_CACHE is not None:
        return _LEGACY_SCHEMA_CACHE

    from mcp.server.fastmcp import FastMCP
    from . import (
        _tools_connection, _tools_workflow, _tools_model, _tools_params,
        _tools_geometry, _tools_snapshot, _tools_physics, _tools_solver,
        _tools_phase1,
    )

    probe = FastMCP("comsol-mcp-legacy-schema")
    for module in (
        _tools_connection, _tools_workflow, _tools_model, _tools_params,
        _tools_geometry, _tools_snapshot, _tools_physics, _tools_solver,
        _tools_phase1,
    ):
        module.register(probe)

    tools = getattr(getattr(probe, "_tool_manager", None), "_tools", {})
    schemas: dict[str, dict[str, Any]] = {}
    for name in LEGACY_FALLBACK_NAMES:
        tool = tools.get(name)
        schema = getattr(tool, "parameters", None)
        if not isinstance(schema, Mapping):
            continue
        # Detach from FastMCP's internal object and close the legacy contract:
        # the original Python signatures have no **kwargs, so an unknown field
        # must be rejected before the managed backend or engine is reached.
        detached = json.loads(json.dumps(dict(schema), ensure_ascii=False))
        detached.setdefault("type", "object")
        detached.setdefault("properties", {})
        detached["additionalProperties"] = False
        schemas[name] = detached

    missing = sorted(LEGACY_FALLBACK_NAMES - schemas.keys())
    if missing:
        raise RuntimeError("legacy FastMCP schema missing for: " + ", ".join(missing))
    _LEGACY_SCHEMA_CACHE = schemas
    return schemas

CONTROL_STATUS_TOOL_NAMES = frozenset({
    "session_health", "model_inspect", "model_adopt",
    "job_list", "job_status", "job_log", "job_result", "job_wait", "job_cancel", "job_reconcile",
})

# Profile selection affects only the static MCP publication.  The backend
# registry and explicit fallback map remain complete in every mode.
_PROFILE_ALWAYS_TOOLS = frozenset({
    "registry_list", "registry_describe", "registry_search", "registry_manifest", "registry_call",
    "operation_describe", "operation_call", "session_health", "model_inspect", "model_adopt",
    "job_list", "job_status", "job_log", "job_result", "job_wait", "job_cancel", "job_reconcile",
    "mcp_tool_audit", "server_info", "check_server_port",
})
_PROFILE_DOMAIN_TOOLS = frozenset({
    "workflow_info", "visible_main_workflow_status", "verify_visible_main_session", "model_tree", "get_parameters",
    "set_parameters", "evaluate_expressions", "get_core_metrics", "run_study_status", "run_feature", "run_study",
    "run_study_async", "ensure_component", "ensure_geometry", "ensure_mesh", "create_feature", "update_feature",
    "delete_feature", "create_physics", "remove_physics", "create_physics_feature", "update_physics_feature",
    "remove_physics_feature", "set_physics_selection", "list_physics", "list_physics_features", "list_solver_config",
    "list_solver_features", "create_solver_config", "configure_solver", "manage_variables", "docs_index", "docs_search",
    "docs_get", "docs_examples", "docs_error_search", "transaction_preview", "transaction_trial", "transaction_apply",
    "transaction_verify", "transaction_recover", "checkpoint_list", "checkpoint_inspect", "checkpoint_diff",
})
_PROFILE_EXPERT_TOOLS = frozenset({
    "code_describe_java", "code_compile_java", "code_execute_java", "code_inspect_run", "docs_index", "docs_search",
    "docs_get", "docs_examples", "docs_error_search", "transaction_preview", "transaction_trial", "transaction_apply",
    "transaction_verify", "transaction_recover", "checkpoint_list", "checkpoint_inspect", "checkpoint_diff",
})


def current_tool_profile(value: str | None = None) -> str:
    profile = (value if value is not None else os.environ.get("COMSOL_MCP_TOOL_PROFILE", "full")).strip().lower()
    if profile not in {"full", "domain", "expert"}:
        raise ExecutionContractError("INVALID_REQUEST", "COMSOL_MCP_TOOL_PROFILE must be full, domain, or expert")
    return profile


def is_tool_published(name: str, *, profile: str | None = None) -> bool:
    selected = current_tool_profile(profile)
    if selected == "full" or name in _PROFILE_ALWAYS_TOOLS:
        return True
    return name in (_PROFILE_DOMAIN_TOOLS if selected == "domain" else _PROFILE_EXPERT_TOOLS)


def published_tool_names(names: list[str] | tuple[str, ...] | set[str], *, profile: str | None = None) -> list[str]:
    return [name for name in names if is_tool_published(name, profile=profile)]


@dataclass(frozen=True, slots=True)
class ActionEntry:
    operation_id: str
    mcp_tool_name: str
    domain: str
    purpose: str
    effect: str
    scope: str
    gate: str
    implementation_status: str
    input_schema: Mapping[str, Any]
    output_contract: str
    route: str
    required_tests: tuple[str, ...]
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        catalog_schema = dict(self.input_schema)
        effective_schema = _effective_input_schema(catalog_schema, self.operation_id)
        return {
            "operation_id": self.operation_id,
            "mcp_tool_name": self.mcp_tool_name,
            "domain": self.domain,
            "purpose": self.purpose,
            "effect": self.effect,
            "scope": self.scope,
            "gate": self.gate,
            "implementation_status": "SUPPORTED_UNVERIFIED" if is_implemented(self.operation_id) else self.implementation_status,
            # ``input_schema`` is the schema clients should actually use on
            # the production wire.  Keep the design catalog form alongside it
            # so a reviewer can see the compatibility delta rather than
            # mistaking a historical ``model_ref: string`` for the live
            # structured ModelRef envelope.
            "input_schema": effective_schema,
            "catalog_input_schema": catalog_schema,
            "output_schema": json.loads(json.dumps(_ACTION_RESULT_SCHEMA)),
            "wire_compatibility": json.loads(json.dumps(_WIRE_COMPATIBILITY)),
            "output_contract": self.output_contract,
            "route": self.route,
            "required_tests": list(self.required_tests),
            "notes": self.notes,
            "executable": is_implemented(self.operation_id) or self.operation_id in LEGACY_FALLBACK_NAMES,
        }


_ACTION_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["success", "data"],
    "properties": {
        "success": {"type": "boolean"},
        "data": {"type": "object"},
        "error": {"type": ["object", "string", "null"]},
        "execution": {"type": "object"},
    },
    "additionalProperties": True,
}

_WIRE_COMPATIBILITY: dict[str, Any] = {
    "transport": "MCP structuredContent plus text mirror; isError equals not success",
    "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
    "execution_fields": ["session_id", "model_ref", "expected_revision", "idempotency_key", "request_id", "rpc_timeout_s", "queue_timeout_s", "execution_timeout_s"],
    "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
    "result_fields": ["success", "data", "error", "execution"],
}


#: The action vocabulary ``definition.component_manage`` enforces itself (its
#: own final ``INVALID_REQUEST`` lists exactly these).  The design catalogue
#: declares ``tag`` in its unconditional ``required`` list, but the operation
#: addresses a tag only for the actions that *name* one component: ``list``
#: enumerates ``model.component()`` and the executable adapter refuses a tag for
#: it ("action 'list' does not accept a tag", Programming Reference
#: ``model.component()`` / javap ``ComponentList``).  The per-action truth is
#: published below and enforced by ``_validate_operation_shape`` before
#: dispatch, so a correct ``{"action": "list"}`` body is not refused for the
#: argument the operation itself forbids (live evidence:
#: ``evidence/phase4_1/runs/20260921T001454Z-g3_1-m1c`` W13_T006 -- the driver's
#: component-list read was answered ``INVALID_REQUEST: missing required
#: operation arguments: tag``, which blocked the case's prerequisite chain).
COMPONENT_MANAGE_ACTIONS: tuple[str, ...] = ("create", "inspect", "list", "remove", "copy")
#: The subset of that vocabulary whose call addresses an existing/new component
#: by tag and therefore requires one.
COMPONENT_MANAGE_TAGGED_ACTIONS: tuple[str, ...] = tuple(
    action for action in COMPONENT_MANAGE_ACTIONS if action != "list"
)


def _effective_input_schema(catalog_schema: Mapping[str, Any], operation_id: str = "") -> dict[str, Any]:
    """Make the published schema describe the actual MCP compatibility wire."""
    # The catalog contains JSON-compatible values.  A JSON round trip gives
    # callers a detached object without mutating the source catalog mapping.
    effective = json.loads(json.dumps(dict(catalog_schema), ensure_ascii=False))
    properties = effective.get("properties")
    if isinstance(properties, dict) and "model_ref" in properties:
        properties["model_ref"] = {
            "type": "object",
            "required": ["schema_version", "session_id", "server_instance_id", "model_tag", "generation"],
            "properties": {
                "schema_version": {"type": "integer", "minimum": 1},
                "session_id": {"type": "string", "minLength": 1},
                "server_instance_id": {"type": "string", "minLength": 1},
                "model_tag": {"type": "string", "minLength": 1},
                "generation": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
            "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
        }
    # The offline index adapter accepts optional provenance selectors when it
    # writes a source.  Older catalog snapshots omitted them even though the
    # production control path has always carried these fields.
    if operation_id == "docs.index" and isinstance(properties, dict):
        properties.setdefault("version", {"type": "string"})
        properties.setdefault("product", {"type": "string"})
    if operation_id == "transaction.preview":
        # Static planning never enters the engine or requires a bound model.
        # Identity remains optional context, unlike apply/trial/restore.
        effective["required"] = [name for name in effective.get("required", [])
                                 if name not in {"session_id", "model_ref", "expected_revision"}]
    if operation_id in {"node.children", "node.find"}:
        # R02 continuation and traversal budgets are additive to the reviewed
        # catalog schema; the engine adapter validates their exact shape.
        properties.setdefault("budget", {
            "type": "object",
            "properties": {"max_nodes": {"type": "integer"}, "max_seconds": {"type": "number"}, "max_rpc": {"type": "integer"}},
            "additionalProperties": False,
            "description": "Traversal budget for the resumable node search; values are validated by the engine adapter.",
        })
    if operation_id == "node.find":
        properties.setdefault("cursor", {"type": "string", "description": "Continuation cursor returned by a truncated search."})
    if operation_id == "definition.component_manage":
        # The catalogue's flat ``required`` list names ``tag`` unconditionally;
        # the operation's own per-action contract does not (see
        # COMPONENT_MANAGE_ACTIONS).  Publish the conditional rule -- required
        # for every action that names a component, forbidden for ``list`` -- so
        # a client reading the schema is told the truth, and enforce it in
        # ``_validate_operation_shape`` for the calls that reach dispatch.
        required = effective.get("required")
        if isinstance(required, list):
            effective["required"] = [name for name in required if name != "tag"]
        effective["allOf"] = [
            {"if": {"properties": {"action": {"const": "list"}}, "required": ["action"]},
             "then": {"not": {"required": ["tag"]}},
             "else": {
                 "properties": {"action": {"enum": list(COMPONENT_MANAGE_TAGGED_ACTIONS)}},
                 "required": ["tag"],
             }},
        ]
    return effective


def _catalog_entries() -> tuple[ActionEntry, ...]:
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"action catalog unavailable: {CATALOG_PATH}") from exc
    operations = raw.get("operations")
    if not isinstance(operations, list):
        raise RuntimeError("action catalog operations must be an array")
    entries: list[ActionEntry] = []
    for item in operations:
        if not isinstance(item, Mapping) or not isinstance(item.get("operation_id"), str):
            continue
        operation_id = item["operation_id"]
        # Import-time value stays side-effect free; ``as_dict`` recomputes it
        # through ``is_implemented`` at call time so the G3 modules (loaded
        # lazily) are reflected without an import cycle.
        status = "SUPPORTED_UNVERIFIED" if operation_id in IMPLEMENTED_OPERATIONS else str(item.get("implementation_status", "PROPOSED_NOT_IMPLEMENTED"))
        entries.append(ActionEntry(
            operation_id=operation_id,
            mcp_tool_name=str(item.get("mcp_tool_name") or operation_id.replace(".", "_")),
            domain=str(item.get("domain") or operation_id.split(".", 1)[0]),
            purpose=str(item.get("purpose") or ""), effect=str(item.get("effect") or ""),
            scope=str(item.get("scope") or ""), gate=str(item.get("gate") or ""),
            implementation_status=status, input_schema=item.get("input_schema") if isinstance(item.get("input_schema"), Mapping) else {},
            output_contract=str(item.get("output_contract") or "ActionResult"), route=str(item.get("route") or ""),
            required_tests=tuple(str(value) for value in item.get("required_tests", []) if isinstance(value, str)),
            notes=str(item.get("notes") or ""),
        ))
    return tuple(entries)


ENTRIES = _catalog_entries()
BY_ID = {entry.operation_id: entry for entry in ENTRIES}
BY_TOOL = {entry.mcp_tool_name: entry for entry in ENTRIES}

# ``code.describe_java`` is the read-only front half of the required
# describe/compile/execute flow.  Older catalog snapshots only listed the
# compile and execute calls, so keep this additive synthetic entry explicit
# instead of pretending it came from the historical catalog.
if "code.describe_java" not in BY_ID:
    _describe_schema = {"type": "object", "properties": {
        "project_id": {"type": "string"}, "source_artifact": {"type": "string"}, "entrypoint": {"type": "string"}},
        "required": ["project_id", "source_artifact"], "additionalProperties": False}
    _describe_entry = ActionEntry("code.describe_java", "code_describe_java", "code", "Describe bound Java source and side effects", "READ", "project", "G2", "SUPPORTED_UNVERIFIED", _describe_schema, "ActionResult", "control plane", ("schema_valid", "normal_path"), "Source is data until explicit trusted_code execution.")
    ENTRIES = (*ENTRIES, _describe_entry)
    BY_ID[_describe_entry.operation_id] = _describe_entry
    BY_TOOL[_describe_entry.mcp_tool_name] = _describe_entry


def _entry(operation_id: str) -> ActionEntry:
    if not isinstance(operation_id, str) or not operation_id:
        raise ExecutionContractError("INVALID_REQUEST", "operation_id is required")
    try:
        return BY_ID[operation_id]
    except KeyError as exc:
        raise ExecutionContractError("UNSUPPORTED_OPERATION", f"unknown registry operation: {operation_id}") from exc


def _legacy_entry(operation_id: str) -> ActionEntry | None:
    """Return the explicit compatibility descriptor for an omitted legacy tool."""
    if operation_id not in LEGACY_FALLBACK_NAMES:
        return None
    effect = LEGACY_TOOL_EFFECTS.get(operation_id)
    if not isinstance(effect, str):
        # A name can only be added to the allowlist together with a server-side
        # effect classification.  Do not make a dynamic fallback permission.
        raise ExecutionContractError("PERMISSION_DENIED", f"legacy fallback has no effect classification: {operation_id}")
    input_schema = _legacy_input_schemas().get(operation_id)
    if input_schema is None:
        # This should be unreachable after _legacy_input_schemas()'s complete
        # set check, but keep the fallback fail-closed if a registration drifts.
        raise ExecutionContractError("UNSUPPORTED_OPERATION", f"legacy fallback schema unavailable: {operation_id}")
    return ActionEntry(
        operation_id=operation_id,
        mcp_tool_name=operation_id,
        domain="legacy",
        purpose="Managed compatibility route for the original MCP tool",
        effect=effect.upper(),
        scope="legacy managed service",
        gate="W01 compatibility mapping plus ordinary service gate",
        implementation_status="SUPPORTED_UNVERIFIED",
        input_schema=input_schema,
        output_contract="success/data/error/execution envelope",
        route="legacy managed backend",
        required_tests=("legacy_fallback", "permission_gate"),
        notes="Hidden from a narrowed static publication profile only; callable through operation_call with this explicit mapping. Input schema is generated from the registered FastMCP function signature; unknown fields are rejected before dispatch.",
    )


def operation_describe(operation_id: str) -> dict[str, Any]:
    """Describe a logical catalog action or an explicitly mapped legacy tool."""
    try:
        return _entry(operation_id).as_dict()
    except ExecutionContractError as exc:
        if exc.code != "UNSUPPORTED_OPERATION":
            raise
        legacy = _legacy_entry(operation_id)
        if legacy is None:
            raise
        return legacy.as_dict()


def registry_list(*, domain: str | None = None, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ExecutionContractError("INVALID_REQUEST", "limit must be an integer between 1 and 500")
    selected = [entry for entry in ENTRIES if not domain or entry.domain == domain]
    start = 0
    if cursor:
        try:
            start = int(cursor)
        except (TypeError, ValueError) as exc:
            raise ExecutionContractError("INVALID_REQUEST", "cursor must be an integer continuation token") from exc
        if start < 0:
            raise ExecutionContractError("INVALID_REQUEST", "cursor must be non-negative")
    rows = [entry.as_dict() for entry in selected[start:start + limit]]
    next_cursor = str(start + len(rows)) if start + len(rows) < len(selected) else None
    return {"operations": rows, "next_cursor": next_cursor, "count": len(rows), "total": len(selected)}


def registry_describe(operation_id: str) -> dict[str, Any]:
    return _entry(operation_id).as_dict()


def registry_search(query: str, *, domain: str | None = None) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ExecutionContractError("INVALID_REQUEST", "query must be a non-empty string")
    terms = [term.lower() for term in re.findall(r"[\w.:-]+", query) if term]
    selected = [entry for entry in ENTRIES if not domain or entry.domain == domain]
    ranked: list[tuple[int, ActionEntry]] = []
    for entry in selected:
        haystack = " ".join((entry.operation_id, entry.mcp_tool_name, entry.domain, entry.purpose, entry.notes)).lower()
        score = sum((3 if term in entry.operation_id.lower() else 2 if term in entry.mcp_tool_name.lower() else 1) for term in terms if term in haystack)
        if score:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], item[1].operation_id))
    return {"query": query, "operations": [{**entry.as_dict(), "score": score} for score, entry in ranked]}


def registry_manifest(profile: str | None = None) -> dict[str, Any]:
    profile = profile or "full"
    if profile not in {"full", "domain", "expert"}:
        raise ExecutionContractError("INVALID_REQUEST", "profile must be full, domain, or expert")
    executable = [entry for entry in ENTRIES if is_implemented(entry.operation_id)]
    if profile == "domain":
        # Domain profile is a presentation filter.  It retains registry_call
        # so an operation absent from a static host can still be selected.
        # G3 (W13-W16) domain actions belong to this profile.
        executable = [entry for entry in executable if entry.domain in {
            "registry", "node", "docs", "transaction", "checkpoint",
            "parameter", "variable", "function", "selection", "geometry", "definition",
            "material", "physics", "mesh", "study", "solver",
        }]
    elif profile == "expert":
        executable = [entry for entry in executable if entry.domain in {"registry", "node", "code", "transaction", "checkpoint"}]
    return {
        "profile": profile,
        "publication_profile": current_tool_profile(),
        "catalog_path": str(CATALOG_PATH),
        "catalog_sha256": __import__("hashlib").sha256(CATALOG_PATH.read_bytes()).hexdigest(),
        "operations": [entry.as_dict() for entry in executable],
        "fallback": {"describe": "operation_describe", "call": "operation_call", "dynamic_tools": False,
                     "legacy_operations": sorted(LEGACY_FALLBACK_NAMES)},
        "limitations": ["COMSOL engine and GUI acceptance remain runtime-scoped and are not implied by registry publication."],
    }


def validate_call(operation_id: str, arguments: Any, *, allow_unbound_identity: bool = False) -> ActionEntry:
    """Validate a catalog operation before it reaches a control/engine call.

    Published registry calls carry identity and idempotency in the managed
    request envelope, so those fields are required by default even when the
    historical catalog put them in the operation body.  Transaction action
    entries are deliberately an exception: their outer transaction supplies
    the identity and gate, while each inner action is only a static plan row.
    Callers for that internal path must opt in explicitly with
    ``allow_unbound_identity=True``.
    """
    legacy = _legacy_entry(operation_id)
    entry = legacy if legacy is not None else _entry(operation_id)
    if legacy is None and not is_implemented(entry.operation_id):
        raise ExecutionContractError("UNSUPPORTED_OPERATION", f"operation is cataloged but not executable in this build: {operation_id}")
    if not isinstance(arguments, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
    schema = _effective_input_schema(entry.input_schema, entry.operation_id)
    if isinstance(schema, Mapping):
        _validate_schema_object(arguments, schema, operation_id)
    required = schema.get("required", []) if isinstance(schema, Mapping) else []
    identity_fields = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key"}
    missing = [name for name in required if name not in arguments and not (allow_unbound_identity and name in identity_fields)]
    if missing:
        raise ExecutionContractError("INVALID_REQUEST", f"missing required operation arguments: {', '.join(missing)}")
    _validate_operation_shape(entry.operation_id, arguments)
    return entry


# The catalog schemas use references into common.schema.json.  The gateway
# keeps that catalog as the source of truth, but it must still reject malformed
# top-level calls before they reach a model.  Referenced values receive their
# operation-specific strict validation at the execution adapter (NodePath and
# TypedValue); this small validator covers the JSON primitives and closed
# operation objects available directly in the catalog without introducing a
# second schema dependency in the control daemon.
_ENVELOPE_FIELDS = frozenset({
    "project_id", "session_id", "model_ref", "expected_revision",
    "idempotency_key", "request_id",
})


def _validate_schema_object(arguments: Mapping[str, Any], schema: Mapping[str, Any], operation_id: str) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return
    allowed = set(properties) | _ENVELOPE_FIELDS
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"{operation_id} has unsupported arguments: {', '.join(unknown)}",
            )
    for name, value in arguments.items():
        if name in _ENVELOPE_FIELDS or name not in properties:
            continue
        _validate_schema_value(value, properties[name], f"{operation_id}.{name}")


def _validate_schema_value(value: Any, schema: Any, label: str) -> None:
    if not isinstance(schema, Mapping):
        return
    if "$ref" in schema:
        # NodePath, TypedValue and the other common definitions have strict
        # executable validators at their adapters.  Do not guess their shape
        # here from the reference string.
        return
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be one of {enum!r}")
    expected_type = schema.get("type")
    if expected_type == "string":
        if not isinstance(value, str):
            raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a string")
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            raise ExecutionContractError("INVALID_REQUEST", f"{label} must not be empty")
        return
    if expected_type == "boolean":
        if type(value) is not bool:
            raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a boolean")
        return
    if expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an integer")
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            raise ExecutionContractError("INVALID_REQUEST", f"{label} is below its minimum")
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            raise ExecutionContractError("INVALID_REQUEST", f"{label} is above its maximum")
        return
    if expected_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a number")
        return
    if expected_type == "array":
        if not isinstance(value, list):
            raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an array")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, f"{label}[{index}]")
        return
    if expected_type == "object" and not isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an object")


def _validate_operation_shape(operation_id: str, arguments: Mapping[str, Any]) -> None:
    """Apply the executable adapter's small pre-engine shape obligations.

    JSON Schema references such as ``NodePath`` and ``TypedValue`` live in a
    shared design file, so the generic validator intentionally does not copy
    those definitions.  The fallback still must reject an empty names list or
    malformed typed action before the no-model gate reports ENGINE_UNRESPONSIVE.
    """
    from ._g2_contract import NodePath, validate_property_set, validate_typed_value

    path_operations = {
        "node.inspect", "node.children", "node.property_schema", "node.property_get",
        "node.property_set", "node.property_index_set", "node.property_entry_set",
    }
    if operation_id in path_operations:
        NodePath.from_wire(arguments.get("path"))
    if operation_id == "node.inspect":
        if "include_values" in arguments and type(arguments["include_values"]) is not bool:
            raise ExecutionContractError("INVALID_REQUEST", "include_values must be a boolean")
    elif operation_id == "node.property_get":
        names = arguments.get("names")
        if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
            raise ExecutionContractError("INVALID_REQUEST", "names must be a non-empty array of strings")
    elif operation_id == "node.property_set":
        validate_property_set(arguments.get("properties"))
    elif operation_id == "node.property_index_set":
        name, indices, value = arguments.get("name"), arguments.get("indices"), arguments.get("value")
        if not isinstance(name, str) or not name:
            raise ExecutionContractError("INVALID_REQUEST", "name is required")
        if not isinstance(indices, list) or not indices or not all(isinstance(item, int) and not isinstance(item, bool) for item in indices):
            raise ExecutionContractError("INVALID_REQUEST", "indices must be a non-empty integer array")
        validate_typed_value(value)
    elif operation_id == "node.property_entry_set":
        for field in ("name", "key"):
            if not isinstance(arguments.get(field), str) or not arguments[field]:
                raise ExecutionContractError("INVALID_REQUEST", f"{field} is required")
        validate_typed_value(arguments.get("value"))
    elif operation_id in {"transaction.preview", "transaction.trial", "transaction.apply"}:
        if not isinstance(arguments.get("actions"), list):
            raise ExecutionContractError("INVALID_REQUEST", "actions must be an array")
    elif operation_id == "docs.index":
        sources = arguments.get("sources")
        if not isinstance(sources, list) or not sources or not all(isinstance(item, str) and item for item in sources):
            raise ExecutionContractError("INVALID_REQUEST", "sources must be a non-empty array of paths")
    elif operation_id == "definition.component_manage":
        # The catalogue requires ``tag`` for every action; the operation itself
        # requires it for the actions that name a component and *refuses* it for
        # ``list`` (which enumerates ``model.component()``).  Enforcing the real
        # per-action rule here keeps the coarse form's strictness -- a create
        # without a tag is still refused before dispatch -- without demanding an
        # argument the operation forbids.
        action = arguments.get("action")
        if not isinstance(action, str) or action not in COMPONENT_MANAGE_ACTIONS:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"action must be one of {'/'.join(COMPONENT_MANAGE_ACTIONS)}")
        if action == "list":
            if arguments.get("tag") is not None:
                raise ExecutionContractError(
                    "INVALID_REQUEST", "action 'list' does not accept a tag")
        elif not isinstance(arguments.get("tag"), str) or not arguments["tag"]:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"tag is required for action {action!r} (only 'list' enumerates the component container "
                "without naming one)")
    if operation_id in {"node.children", "node.find"}:
        budget = arguments.get("budget")
        if budget is not None:
            if not isinstance(budget, Mapping):
                raise ExecutionContractError("INVALID_REQUEST", "budget must be an object")
            unknown = sorted(set(budget) - {"max_nodes", "max_seconds", "max_rpc"})
            if unknown:
                raise ExecutionContractError("INVALID_REQUEST", f"budget has unsupported fields: {', '.join(unknown)}")
            for name in ("max_nodes", "max_rpc"):
                if name in budget and (isinstance(budget[name], bool) or not isinstance(budget[name], int) or budget[name] < 1):
                    raise ExecutionContractError("INVALID_REQUEST", f"budget.{name} must be a positive integer")
            if "max_seconds" in budget and (isinstance(budget["max_seconds"], bool) or not isinstance(budget["max_seconds"], (int, float)) or budget["max_seconds"] <= 0):
                raise ExecutionContractError("INVALID_REQUEST", "budget.max_seconds must be a positive number")
        if operation_id == "node.find" and arguments.get("cursor") is not None and not isinstance(arguments.get("cursor"), str):
            raise ExecutionContractError("INVALID_REQUEST", "cursor must be a string")


def operation_for_tool(name: str) -> str:
    if name in BY_TOOL:
        return BY_TOOL[name].operation_id
    if name in BY_ID:
        return name
    raise ExecutionContractError("UNSUPPORTED_OPERATION", f"unknown operation tool: {name}")
