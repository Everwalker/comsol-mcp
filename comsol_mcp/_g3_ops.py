"""Aggregator for the G3 domain-operation modules (W13-W16).

The G2 layer publishes its bounded operation set in ``_g2_registry``.  The G3
domain modules publish theirs here: one module per workstream, each exporting
``OPERATIONS: dict[str, Callable[[Any, str, dict], dict]]`` keyed by the
catalogue ``operation_id`` (for example ``"parameter.set"``).

A per-domain function is called by the dispatcher as::

    data = OPERATIONS["parameter.set"](worker, model_tag, arguments)

It returns the operation's ``data`` dictionary on success and raises
``comsol_mcp._g2_contract.ExecutionContractError`` on failure; it never wraps
its own result in a ``{"success": ...}`` envelope, because the control plane
owns that envelope.

This module is import-light and side-effect free apart from importing the
domain modules themselves:

* a module that does not exist yet (an unimplemented workstream) is skipped,
  and the skip is recorded in ``SKIPPED_MODULES``;
* a duplicate ``operation_id`` between two modules is a hard error, because a
  silent last-wins dispatch would make the published tool surface depend on
  import order.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping, cast

from ._g2_registry import BY_ID

#: The G3 domain modules, in workstream order.  W14-W16 are scheduled; this
#: tuple is the single place that names them.  ``_g3_runtime`` publishes the
#: runtime/licence inspection operations W15's T042 acceptance needs, and
#: ``_g3_results`` the minimal sampling adapter recorded as W16 acceptance
#: infrastructure.
_MODULES = (
    "_g3_w13",
    "_g3_w14",
    "_g3_w15",
    "_g3_w16",
    "_g3_runtime",
    "_g3_results",
    "_probe_manage",
    "_artifact_store",
    "_g3_w18",
)

#: Effects for operations the design catalogue does not (yet) describe.  Every
#: W13 operation is present in 02_ACTION_CATALOG.json, so this table is a
#: safety net for a versioned adapter rather than a second source of truth; a
#: miss falls back to ``WRITE`` (fail closed: a write must be isolated).
_FALLBACK_EFFECTS: dict[str, str] = {
    "parameter.list": "READ",
    "parameter.get": "READ",
    "parameter.set": "WRITE",
    "parameter.remove": "WRITE",
    "parameter.group_manage": "WRITE",
    "variable.list": "READ",
    "variable.get": "READ",
    "variable.set": "WRITE",
    "variable.remove": "WRITE",
    "variable.group_create": "WRITE",
    "variable.selection_set": "WRITE",
    "function.list": "READ",
    "function.create": "WRITE",
    "function.inspect": "READ",
    "function.update": "WRITE",
    "function.remove": "WRITE",
    "function.data_import": "WRITE",
    "function.data_reload": "WRITE",
    "function.evaluate": "EVALUATE",
    "selection.list": "READ",
    "selection.create": "WRITE",
    "selection.inspect": "READ",
    "selection.update": "WRITE",
    "selection.remove": "WRITE",
    "selection.entities": "READ",
    "selection.measure": "EVALUATE",
    "selection.query_spatial": "EVALUATE",
    "selection.adjacency": "READ",
    "selection.validate": "EVALUATE",
    "dataset.list": "READ",
    "dataset.create": "WRITE",
    "dataset.inspect": "READ",
    "dataset.update": "WRITE",
    "dataset.remove": "WRITE",
    "dataset.solution_indices": "READ",
    "result.evaluate": "EVALUATE",
    "result.at_points": "EVALUATE",
    "result.sample_path": "EVALUATE",
    "result.numerical_manage": "DYNAMIC",
    "result.table_manage": "DYNAMIC",
    "result.field_export": "FILE_WRITE",
    "plot.list": "READ",
    "plot.group_create": "WRITE",
    "plot.feature_create": "WRITE",
    "plot.update": "WRITE",
    "plot.remove": "WRITE",
    "plot.render": "READ",
    "plot.geometry_render": "READ",
    "plot.view_manage": "DYNAMIC",
    "export.list": "READ",
    "export.create": "WRITE",
    "export.update": "WRITE",
    "export.run": "COMPUTE",
    "export.remove": "WRITE",
}

#: Effects that are not a pure model read and therefore need the isolated
#: execution path.  ``EVALUATE`` is included: it may create or mutate ephemeral
#: nodes (the catalogue says so explicitly) and the measurement tool's
#: selection state is such a transient.
NON_READ_EFFECTS = frozenset({"WRITE", "FILE_WRITE", "DYNAMIC", "EVALUATE", "COMPUTE"})


def _load_modules() -> tuple[dict[str, Callable[..., dict[str, Any]]], dict[str, str], list[dict[str, str]]]:
    operations: dict[str, Callable[..., dict[str, Any]]] = {}
    origins: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    for name in _MODULES:
        try:
            module = importlib.import_module(f".{name}", __package__)
        except ImportError as exc:
            # An unimplemented workstream is skipped, and the reason is kept so
            # the caller can tell "not written yet" from "broken".
            skipped.append({"module": name, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        table = getattr(module, "OPERATIONS", None)
        if not isinstance(table, Mapping):
            raise RuntimeError(f"{name}.OPERATIONS must be a mapping of operation_id -> callable")
        for operation_id, function in table.items():
            if not isinstance(operation_id, str) or not operation_id:
                raise RuntimeError(f"{name}.OPERATIONS has a non-string operation id: {operation_id!r}")
            if not callable(function):
                raise RuntimeError(f"{name}.OPERATIONS[{operation_id!r}] is not callable")
            if operation_id in operations:
                raise RuntimeError(
                    f"operation {operation_id!r} is published by both {origins[operation_id]} and {name}"
                )
            operations[operation_id] = cast(Callable[..., dict[str, Any]], function)
            origins[operation_id] = name
    return operations, origins, skipped


DISPATCH, OPERATION_ORIGINS, SKIPPED_MODULES = _load_modules()

#: Published operation ids; consumed by ``_g2_registry.is_implemented`` and by
#: the control plane's preflight.
IMPLEMENTED_OPERATIONS: frozenset[str] = frozenset(DISPATCH)


def _effect_sources() -> tuple[dict[str, str], dict[str, str]]:
    effects: dict[str, str] = {}
    sources: dict[str, str] = {}
    for operation_id in sorted(DISPATCH):
        entry = BY_ID.get(operation_id)
        effect = getattr(entry, "effect", None) if entry is not None else None
        if isinstance(effect, str) and effect:
            effects[operation_id] = effect
            sources[operation_id] = "catalog"
            continue
        fallback = _FALLBACK_EFFECTS.get(operation_id)
        if fallback:
            effects[operation_id] = fallback
            sources[operation_id] = "explicit_fallback_table"
            continue
        effects[operation_id] = "WRITE"
        sources[operation_id] = "fail_closed_default"
    return effects, sources


EFFECTS, EFFECT_SOURCES = _effect_sources()

#: Every operation whose effect is not a pure model read must run isolated.
#: The parent re-checks this set against the catalogue before dispatching.
REQUIRES_ISOLATION: frozenset[str] = frozenset(
    operation_id for operation_id, effect in EFFECTS.items() if effect not in {"READ"}
)


def is_implemented(operation_id: Any) -> bool:
    return isinstance(operation_id, str) and operation_id in IMPLEMENTED_OPERATIONS


def effect_of(operation_id: Any) -> str | None:
    return EFFECTS.get(operation_id) if isinstance(operation_id, str) else None


def requires_isolation(operation_id: Any) -> bool:
    return isinstance(operation_id, str) and operation_id in REQUIRES_ISOLATION


def dispatch(operation_id: str, worker: Any, model_tag: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Call one G3 operation; a missing operation is a structured refusal."""
    from ._g2_contract import ExecutionContractError

    if not is_implemented(operation_id):
        raise ExecutionContractError("UNSUPPORTED_OPERATION", f"no G3 implementation for {operation_id!r}")
    return DISPATCH[operation_id](worker, model_tag, dict(arguments or {}))


def describe() -> dict[str, Any]:
    """Small, truthful inventory of the G3 surface for a health/diagnostics call."""
    return {
        "modules": list(_MODULES),
        "skipped_modules": list(SKIPPED_MODULES),
        "implementation_origins": dict(OPERATION_ORIGINS),
        "operation_count": len(IMPLEMENTED_OPERATIONS),
        "operations": sorted(IMPLEMENTED_OPERATIONS),
        "effects": dict(EFFECTS),
        "effect_sources": dict(EFFECT_SOURCES),
        "requires_isolation": sorted(REQUIRES_ISOLATION),
    }


__all__ = [
    "DISPATCH",
    "EFFECTS",
    "EFFECT_SOURCES",
    "IMPLEMENTED_OPERATIONS",
    "NON_READ_EFFECTS",
    "OPERATION_ORIGINS",
    "REQUIRES_ISOLATION",
    "SKIPPED_MODULES",
    "describe",
    "dispatch",
    "effect_of",
    "is_implemented",
    "requires_isolation",
]
