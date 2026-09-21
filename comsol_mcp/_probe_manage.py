"""Model Definitions Probe Management Service for COMSOL MCP (F10).

Distinguishes Model Definitions Probes from Results Derived Values:
- Model Definitions Probes live under ``model.probe()`` or ``model.component(<comp>).probe()``.
- Results Derived Values live under ``model.result().numerical()``.
- Provides probe list, create, inspect, update, remove, and history retrieval.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError
from ._g3_common import require_mapping, require_string
from ._g2_engine import _call, _model as bound_model

SUPPORTED_PROBE_TYPES = frozenset({
    "DomainProbe",
    "BoundaryProbe",
    "PointProbe",
    "GlobalProbe",
})


def _probe_container(model: Any, comp_tag: str | None = None) -> Any:
    """Resolve probe container from model definition or component."""
    if comp_tag:
        comp_node = _call(model, "component", comp_tag)
        return _call(comp_node, "probe")
    try:
        return _call(model, "probe")
    except Exception:
        # If model.probe() is unavailable, check first component
        try:
            comps = _call(_call(model, "component"), "tags")
            if comps:
                comp_node = _call(model, "component", comps[0])
                return _call(comp_node, "probe")
        except Exception:
            pass
        raise ExecutionContractError("API_UNSUPPORTED", "Model does not support definition probe container")


def probe_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """List all model definition probes."""
    model = bound_model(worker, model_tag)
    container = _probe_container(model)
    tags = list(_call(container, "tags"))

    items: list[dict[str, Any]] = []
    for tag in tags:
        try:
            node = _call(container, "get", tag)
            typ = str(_call(node, "getType")) if hasattr(node, "getType") else "Probe"
            expr = str(_call(node, "getString", "expr")) if hasattr(node, "getString") else None
            table = str(_call(node, "getString", "table")) if hasattr(node, "getString") else None
            items.append({
                "tag": tag,
                "type_id": typ,
                "expression": expr,
                "table": table,
            })
        except Exception:
            items.append({"tag": tag, "type_id": "Probe"})

    return {
        "probes": items,
        "count": len(items),
        "tags": tags,
    }


def probe_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Create a model definition probe."""
    tag = require_string(arguments.get("tag"), "tag")
    type_id = require_string(arguments.get("type_id"), "type_id")
    definition = require_mapping(arguments.get("definition", {}), "definition")

    if type_id not in SUPPORTED_PROBE_TYPES:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"Probe type {type_id!r} not supported; valid: {sorted(SUPPORTED_PROBE_TYPES)}",
        )

    model = bound_model(worker, model_tag)
    container = _probe_container(model)
    tags = list(_call(container, "tags"))
    if tag in tags:
        raise ExecutionContractError("TAG_CONFLICT", f"Probe {tag!r} already exists")

    node = _call(container, "create", tag, type_id)

    applied: list[str] = []
    failed: list[dict[str, Any]] = []
    for k, v in definition.items():
        try:
            _call(node, "set", k, v)
            applied.append(k)
        except Exception as exc:
            failed.append({"property": k, "error": str(exc)})

    return {
        "tag": tag,
        "type_id": type_id,
        "created": True,
        "applied": applied,
        "failed": failed,
    }


def probe_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Remove a model definition probe."""
    tag = require_string(arguments.get("tag"), "tag")
    model = bound_model(worker, model_tag)
    container = _probe_container(model)
    tags = list(_call(container, "tags"))
    if tag not in tags:
        raise ExecutionContractError("NODE_NOT_FOUND", f"Probe {tag!r} does not exist")

    _call(container, "remove", tag)
    remaining = list(_call(container, "tags"))
    return {
        "tag": tag,
        "removed": True,
        "verified_removed": tag not in remaining,
    }
