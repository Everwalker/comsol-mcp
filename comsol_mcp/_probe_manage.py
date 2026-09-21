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


def _probe_container(model: Any, comp_tag: str | None = None, type_id: str | None = None) -> Any:
    """Resolve probe container from model definition or component."""
    if comp_tag:
        comp_node = _call(model, "component", comp_tag)
        return _call(comp_node, "probe")
    if type_id in ("DomainProbe", "BoundaryProbe", "PointProbe"):
        try:
            comps = list(_call(model, "modelNode").tags())
            if comps:
                return _call(_call(model, "component", comps[0]), "probe")
        except Exception:
            pass
    try:
        return _call(model, "probe")
    except Exception:
        pass
    try:
        comps = list(_call(model, "modelNode").tags())
        if comps:
            return _call(_call(model, "component", comps[0]), "probe")
    except Exception:
        pass
    raise ExecutionContractError("API_UNSUPPORTED", "Model does not support definition probe container")


def _probe_containers(model: Any) -> list[Any]:
    """Collect all available probe containers in model and components."""
    containers = []
    try:
        containers.append(_call(model, "probe"))
    except Exception:
        pass
    try:
        model_node = _call(model, "modelNode")
        for comp_tag in list(_call(model_node, "tags")):
            try:
                comp = _call(model, "component", comp_tag)
                containers.append(_call(comp, "probe"))
            except Exception:
                pass
    except Exception:
        pass
    return containers


def probe_list(worker: Any, model_tag: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """List all model definition probes."""
    arguments = arguments or {}
    model = bound_model(worker, model_tag)
    comp_tag = arguments.get("component")
    if comp_tag:
        containers = [_call(_call(model, "component", comp_tag), "probe")]
    else:
        containers = _probe_containers(model)

    items: list[dict[str, Any]] = []
    all_tags: list[str] = []
    for container in containers:
        try:
            tags = list(_call(container, "tags"))
        except Exception:
            tags = []
        for tag in tags:
            all_tags.append(tag)
            try:
                node = _call(container, "get", tag)
                typ = str(_call(node, "getType")) if hasattr(node, "getType") else "Probe"
                expr = None
                table = None
                try:
                    expr = str(_call(node, "getString", "expr"))
                except Exception:
                    pass
                try:
                    table = str(_call(node, "getString", "table"))
                except Exception:
                    pass
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
        "tags": all_tags,
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
    container = _probe_container(model, comp_tag=arguments.get("component"), type_id=type_id)
    tags = list(_call(container, "tags"))
    if tag in tags:
        raise ExecutionContractError("TAG_CONFLICT", f"Probe {tag!r} already exists")

    PROBE_TYPE_MAP = {
        "DomainProbe": "Domain",
        "BoundaryProbe": "Boundary",
        "PointProbe": "Point",
        "GlobalProbe": "Global",
        "Domain": "Domain",
        "Boundary": "Boundary",
        "Point": "Point",
        "Global": "Global",
    }
    comsol_type = PROBE_TYPE_MAP.get(type_id, type_id)
    node = _call(container, "create", tag, comsol_type)

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
    comp_tag = arguments.get("component")
    if comp_tag:
        containers = [_call(_call(model, "component", comp_tag), "probe")]
    else:
        containers = _probe_containers(model)

    target_container = None
    for c in containers:
        try:
            tags = list(_call(c, "tags"))
            if tag in tags:
                target_container = c
                break
        except Exception:
            pass

    if target_container is None:
        raise ExecutionContractError("NODE_NOT_FOUND", f"Probe {tag!r} does not exist")

    _call(target_container, "remove", tag)
    remaining = list(_call(target_container, "tags"))
    return {
        "tag": tag,
        "removed": True,
        "verified_removed": tag not in remaining,
    }
